const CARD_VERSION = "0.3.0";
const UI_POLICY_TYPE = "temporary_guest/ui_policy";
const DASHBOARD_GUARD_KEY = "__temporaryGuestDashboardGuard";
const DASHBOARD_PATH_PATTERN = /^\/[A-Za-z0-9][A-Za-z0-9_-]*(?:\/[A-Za-z0-9][A-Za-z0-9_-]*)*$/;

function dashboardPathAllowed(pathname, dashboardPath) {
  return pathname === dashboardPath || pathname.startsWith(`${dashboardPath}/`);
}

class TemporaryGuestDashboardGuard {
  constructor() {
    this._policy = null;
    this._request = null;
    this._retryTimer = null;
    this._listening = false;
    this._onLocationChanged = () => void this._refreshAndEnforce();
  }

  start() {
    void this._refreshAndEnforce();
  }

  _homeAssistant() {
    return document.querySelector("home-assistant")?.hass;
  }

  _scheduleRetry() {
    if (this._retryTimer !== null) return;
    this._retryTimer = window.setTimeout(() => {
      this._retryTimer = null;
      void this._refreshAndEnforce();
    }, 1000);
  }

  _setListening(enabled) {
    if (enabled === this._listening) return;
    const action = enabled ? "addEventListener" : "removeEventListener";
    window[action]("location-changed", this._onLocationChanged);
    window[action]("popstate", this._onLocationChanged);
    this._listening = enabled;
  }

  async _refreshAndEnforce() {
    const hass = this._homeAssistant();
    if (!hass?.user?.id || typeof hass.callWS !== "function") {
      this._scheduleRetry();
      return;
    }

    if (this._request !== null) {
      await this._request;
      this._enforce();
      return;
    }

    const userId = String(hass.user.id);
    this._request = hass
      .callWS({ type: UI_POLICY_TYPE })
      .then((result) => {
        const currentHass = this._homeAssistant();
        if (String(currentHass?.user?.id || "") !== userId) return;
        const dashboardPath = result?.dashboard_path;
        const managed =
          result?.managed === true &&
          typeof dashboardPath === "string" &&
          DASHBOARD_PATH_PATTERN.test(dashboardPath);
        this._policy = managed ? { managed: true, dashboardPath } : null;
        this._setListening(managed);
      })
      .catch(() => {
        this._policy = null;
        this._setListening(false);
        this._scheduleRetry();
      })
      .finally(() => {
        this._request = null;
      });

    await this._request;
    this._enforce();
  }

  _enforce() {
    const dashboardPath = this._policy?.dashboardPath;
    if (!dashboardPath || dashboardPathAllowed(window.location.pathname, dashboardPath)) {
      return;
    }
    window.history.replaceState(null, "", dashboardPath);
    window.dispatchEvent(
      new CustomEvent("location-changed", { detail: { replace: true } }),
    );
  }
}

if (window[DASHBOARD_GUARD_KEY]?.start) {
  window[DASHBOARD_GUARD_KEY].start();
} else {
  window[DASHBOARD_GUARD_KEY] = new TemporaryGuestDashboardGuard();
  window[DASHBOARD_GUARD_KEY].start();
}

class TemporaryGuestCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._loaded = false;
    this._busy = false;
    this._password = null;
    this._notice = "";
    this._error = "";
    this._status = null;
    this._hideTimer = null;
  }

  setConfig(config) {
    if (!config || config.type !== "custom:temporary-guest-card") {
      throw new Error("type 必须是 custom:temporary-guest-card");
    }
    this._config = { name: "临时用户管理", reveal_seconds: 30, ...config };
    const seconds = Number(this._config.reveal_seconds);
    this._config.reveal_seconds = Number.isFinite(seconds)
      ? Math.min(300, Math.max(5, seconds))
      : 30;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      void this._call("temporary_guest/status");
    }
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig() {
    return { type: "custom:temporary-guest-card", name: "临时用户管理" };
  }

  disconnectedCallback() {
    this._clearHideTimer();
    this._password = null;
  }

  async _call(type, payload = {}) {
    if (!this._hass || this._busy) return;
    this._busy = true;
    this._error = "";
    this._notice = "";
    this._render();
    try {
      const result = await this._hass.callWS({ type, ...payload });
      if (type === "temporary_guest/status") {
        this._status = result;
      } else if (type === "temporary_guest/reveal") {
        this._showPassword(result.password);
      } else {
        if (result && typeof result === "object") {
          const { password, ...status } = result;
          this._status = status;
          if (password) this._showPassword(password);
        }
      }
      if (type === "temporary_guest/rotate") this._notice = "密码已刷新，旧会话已注销";
      if (type === "temporary_guest/set_active") {
        this._notice = payload.active ? "临时用户已启用" : "临时用户已停用并注销";
      }
      if (type === "temporary_guest/set_auto_rotate") {
        this._notice = payload.enabled ? "每日自动刷新已开启" : "每日自动刷新已关闭";
      }
    } catch (error) {
      this._error = this._friendlyError(error);
      if (type !== "temporary_guest/reveal") this._hidePassword();
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _showPassword(password) {
    this._password = String(password || "");
    this._clearHideTimer();
    this._hideTimer = window.setTimeout(
      () => this._hidePassword(),
      this._config.reveal_seconds * 1000,
    );
  }

  _hidePassword() {
    this._clearHideTimer();
    this._password = null;
    this._render();
  }

  _clearHideTimer() {
    if (this._hideTimer !== null) {
      window.clearTimeout(this._hideTimer);
      this._hideTimer = null;
    }
  }

  async _copyPassword() {
    if (!this._password) return;
    try {
      await navigator.clipboard.writeText(this._password);
      this._notice = "密码已复制；请通过可信渠道发送";
      this._error = "";
    } catch (_error) {
      this._error = "浏览器禁止读取剪贴板，请手动选择并复制密码";
    }
    this._render();
  }

  async _handleAction(action, element) {
    if (this._busy) return;
    switch (action) {
      case "reveal":
        if (this._password) this._hidePassword();
        else await this._call("temporary_guest/reveal");
        break;
      case "copy":
        await this._copyPassword();
        break;
      case "rotate":
        if (
          window.confirm(
            "立即刷新密码？临时用户现有的浏览器和 App 会话将被注销。",
          )
        ) {
          await this._call("temporary_guest/rotate");
        }
        break;
      case "active": {
        const target = !this._status?.active;
        const prompt = target
          ? "启用临时用户？其当前密码将可以登录。"
          : "停用临时用户？其浏览器和 App 会话将立即注销。";
        if (window.confirm(prompt)) {
          await this._call("temporary_guest/set_active", { active: target });
        }
        break;
      }
      case "auto":
        await this._call("temporary_guest/set_auto_rotate", {
          enabled: Boolean(element.checked),
        });
        break;
      case "reload":
        await this._call("temporary_guest/status");
        break;
    }
  }

  _friendlyError(error) {
    const code = String(error?.code || "");
    const message = String(error?.message || "");
    if (code === "unauthorized" || /unauthorized/i.test(message)) {
      return "只有 Home Assistant 所有者（Owner）可以使用此卡片";
    }
    if (code === "not_configured") return "Temporary Guest 集成尚未配置";
    return message || "操作失败，请检查 Home Assistant 日志";
  }

  _formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(this._hass?.locale?.language || undefined);
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const status = this._status;
    const active = Boolean(status?.active);
    const auto = Boolean(status?.auto_rotate);
    const password = this._password
      ? this._escape(this._password)
      : "••••••••••••••••••••••••";
    const disabled = this._busy || !status;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .wrap { padding: 16px; color: var(--primary-text-color); }
        .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .identity { min-width: 0; }
        .name { font-size: 1.05rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
        .username { color: var(--secondary-text-color); margin-top: 3px; font-family: monospace; }
        .pill { padding: 5px 10px; border-radius: 999px; font-size: .82rem; white-space: nowrap; }
        .on { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 14%, transparent); }
        .off { color: var(--error-color, #b71c1c); background: color-mix(in srgb, var(--error-color, #b71c1c) 12%, transparent); }
        .password { margin: 18px 0 10px; padding: 13px; border-radius: 10px; background: var(--secondary-background-color); display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        code { overflow-wrap: anywhere; user-select: all; font-size: 1rem; letter-spacing: .03em; }
        .meta { display: grid; grid-template-columns: max-content 1fr; gap: 7px 12px; color: var(--secondary-text-color); font-size: .9rem; margin: 14px 0; }
        .meta .value { color: var(--primary-text-color); text-align: right; }
        .actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
        button { appearance: none; border: 1px solid var(--divider-color); border-radius: 9px; background: var(--card-background-color); color: var(--primary-text-color); padding: 10px 8px; cursor: pointer; font: inherit; }
        button.primary { border-color: var(--primary-color); color: var(--primary-color); }
        button.danger { border-color: var(--error-color); color: var(--error-color); }
        button:disabled { opacity: .45; cursor: default; }
        .toggle { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--divider-color); }
        input[type="checkbox"] { width: 20px; height: 20px; accent-color: var(--primary-color); }
        .message { min-height: 20px; margin-top: 12px; font-size: .88rem; }
        .notice { color: var(--success-color, #2e7d32); }
        .error { color: var(--error-color); }
        .foot { color: var(--secondary-text-color); font-size: .75rem; margin-top: 12px; }
        @media (max-width: 420px) { .actions { grid-template-columns: 1fr; } }
      </style>
      <ha-card header="${this._escape(this._config.name)}">
        <div class="wrap">
          <div class="top">
            <div class="identity">
              <div class="name">${this._escape(status?.display_name || "正在读取…")}</div>
              <div class="username">${this._escape(status?.username || "")}</div>
            </div>
            <span class="pill ${active ? "on" : "off"}">${
              status ? (active ? "已启用" : "已停用") : "未连接"
            }</span>
          </div>

          <div class="password">
            <code aria-label="${this._password ? "当前密码" : "密码已隐藏"}">${password}</code>
            <button data-action="reveal" ${disabled ? "disabled" : ""}>${
              this._password ? "隐藏" : "显示"
            }</button>
          </div>

          <div class="meta">
            <span>上次刷新</span><span class="value">${this._escape(this._formatDate(status?.last_rotated))}</span>
            <span>下次刷新</span><span class="value">${this._escape(this._formatDate(status?.next_rotation))}</span>
            <span>刷新时间</span><span class="value">${this._escape(status?.rotation_time || "—")}</span>
            <span>登录范围</span><span class="value">${status?.local_only ? "仅本地网络" : "允许远程"}</span>
            <span>实体权限</span><span class="value">${
              status
                ? `${Number(status.read_only_entity_count || 0)} 个只读 · ${Number(status.control_entity_count || 0)} 个可控制`
                : "—"
            }</span>
          </div>

          <div class="actions">
            <button class="primary" data-action="copy" ${
              !this._password || this._busy ? "disabled" : ""
            }>复制密码</button>
            <button class="primary" data-action="rotate" ${disabled ? "disabled" : ""}>立即刷新密码</button>
            <button class="${active ? "danger" : "primary"}" data-action="active" ${disabled ? "disabled" : ""}>${
              active ? "停用临时用户" : "启用临时用户"
            }</button>
            <button data-action="reload" ${this._busy ? "disabled" : ""}>重新读取状态</button>
          </div>

          <label class="toggle">
            <span>每天 ${this._escape(status?.rotation_time || "08:00:00")} 自动刷新</span>
            <input type="checkbox" data-action="auto" ${auto ? "checked" : ""} ${disabled ? "disabled" : ""}>
          </label>

          <div class="message" aria-live="polite">
            ${this._busy ? "正在处理…" : ""}
            ${this._notice ? `<span class="notice">${this._escape(this._notice)}</span>` : ""}
            ${this._error ? `<span class="error">${this._escape(this._error)}</span>` : ""}
          </div>
          <div class="foot">实体白名单请在“设置 → 设备与服务 → Temporary Guest → 配置”中修改。密码显示 ${this._config.reveal_seconds} 秒后会从页面清除。仅 Owner 可调用后端操作。</div>
        </div>
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll("[data-action]").forEach((element) => {
      element.addEventListener("click", (event) => {
        void this._handleAction(event.currentTarget.dataset.action, event.currentTarget);
      });
    });
  }
}

if (!customElements.get("temporary-guest-card")) {
  customElements.define("temporary-guest-card", TemporaryGuestCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "temporary-guest-card",
    name: "Temporary Guest",
    description: "Owner-only temporary Home Assistant guest account management",
    preview: true,
  });
  console.info(`%c TEMPORARY-GUEST-CARD %c v${CARD_VERSION} `, "color:white;background:#03a9f4;font-weight:700", "color:#03a9f4;background:white");
}
