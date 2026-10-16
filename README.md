# Temporary Guest for Home Assistant

通过 Home Assistant 网页管理一个专用临时用户：

- 首次配置时自动创建非管理员用户；
- 每天在指定时间（默认 `08:00:00`）生成新密码；
- Owner 可在 Lovelace 卡片中显示、复制或手动刷新密码；
- Owner 可启用或停用临时用户，也可以关闭自动刷新；
- Owner 可在集成选项中分别选择“仅可查看”和“可查看并控制”的实体；
- 未选中的实体由 Home Assistant 服务端拒绝，两个清单都留空时不允许访问任何实体；
- 换密或停用时撤销该临时用户的全部浏览器、App 和长期访问令牌会话；
- 不需要 Shell、SSH、外部服务器或 Owner 长期访问令牌。

> 当前版本要求 **Home Assistant 2026.10.1 或更新的 2026.10.x**，实际接口核验版本为 2026.10.1。集成调用了 HA 自身用户管理所使用的认证接口；升级到新的大版本前，请先查看发布说明并测试。

## 安全边界

只有 Home Assistant 的真正所有者（Owner）可以调用卡片后端。普通管理员、普通用户，即使知道 WebSocket 命令名称，也会被服务端拒绝。

Home Assistant 只保存密码哈希，无法读取现有密码。为了满足“稍后仍能查看当前密码”的要求，本集成会把它生成的当前密码写入 HA 的私有存储文件（`private=True`），不会写入实体状态、属性、Recorder、通知或日志。需要注意：

- Home Assistant 配置目录的管理员和完整备份持有者仍能读到该密码；
- 不要把 HA 备份交给临时用户；
- 不要把密码卡片放进访客仪表盘；
- 卡片显示密码后，默认 30 秒自动从页面清除；
- 通过聊天软件发送密码时，第三方聊天服务仍可能保存它。

本集成会把 `mzuser` 从 HA 默认的普通用户组移入专用限制组，只给所选实体授予 `read` 或 `read + control`。因此未选中实体的状态和控制请求会在服务端被拒绝。修改清单时会先注销该用户的全部会话，再应用新权限。

Lovelace 的“按用户隐藏页面”、子视图和 Kiosk Mode 仍然只是界面控制，不是页面级访问控制。实体白名单可以保护实体状态与控制，但不能保证仪表盘配置、区域名称等非实体元数据完全不可见。不要在访客可加载的仪表盘配置中写入敏感文字或秘密。

## 通过 HACS 安装

### 1. 发布仓库

当前目录已经是完整 HACS 集成仓库。将它上传到你自己的 GitHub 仓库，例如：

```text
https://github.com/<你的用户名>/ha-temporary-guest
```

发布前把 [`manifest.json`](custom_components/temporary_guest/manifest.json) 中的：

```json
"documentation": "https://github.com/OWNER/ha-temporary-guest"
```

替换为实际仓库地址，并创建一个版本标签，例如 `v0.2.0`。

### 2. 导入 HACS

1. 打开 **HACS → 集成**。
2. 右上角菜单选择 **自定义存储库**。
3. 填入仓库 URL，类别选择 **集成**。
4. 安装 **Temporary Guest**。
5. 重启 Home Assistant。

### 3. 添加集成

1. 以 Owner 登录。
2. 打开 **设置 → 设备与服务 → 添加集成**。
3. 搜索 **Temporary Guest / 临时用户**。
4. 填写用户名、显示名、刷新时间和密码长度（默认用户名为 `mzuser`）。
5. 在“仅可查看的实体”中选择只允许查看状态的实体。
6. 在“可查看并控制的实体”中选择允许操作的实体；同一实体出现在两组时以可控制为准。

两个实体清单都留空时，`mzuser` 可以登录但看不到任何实体。如果临时用户需要从互联网登录，不要勾选“只允许从本地网络登录”。用户名会自动转换为小写；为了避免接管现有账号，已经存在的用户名会被拒绝。

之后可随时打开 **设置 → 设备与服务 → Temporary Guest → 配置** 修改实体清单。保存后旧会话立即注销，访客需要重新登录。

### 4. 注册卡片资源

打开 **设置 → 仪表盘 → 右上角菜单 → 资源 → 添加资源**：

```text
URL: /temporary_guest/temporary-guest-card.js?v=0.2.0
类型: JavaScript 模块
```

如果界面中没有“资源”，请先在用户资料页启用高级模式。

### 5. 添加管理卡片

在只给 Owner 使用的仪表盘中添加“手动”卡片：

```yaml
type: custom:temporary-guest-card
name: 临时用户管理
reveal_seconds: 30
```

`reveal_seconds` 可设置为 `5` 至 `300` 秒。

### 6. 建立访客面板

1. 打开 **设置 → 仪表盘 → 添加仪表盘**，建立“访客面板”，不要勾选“仅管理员”。
2. 只加入白名单实体对应的卡片；访客首页不要设为子视图，二级页面才启用“子视图”。
3. 登录一次 `mzuser`，在个人资料中把访客面板设为默认仪表盘。
4. 可用视图可见性或 HACS Kiosk Mode 隐藏其他入口，但这些只负责界面整理，真正的实体安全边界仍是本集成写入的服务端白名单。

密码管理卡绝不能放进访客面板。即使误放，后端仍会拒绝非 Owner 调用，但页面不应向访客暴露管理入口。

## 工作方式

```text
Owner 的管理卡片
        │ 当前网页登录会话（不保存额外令牌）
        ▼
Owner-only WebSocket 命令
        │
        ├─ 改密码并撤销该访客全部会话
        ├─ 启用/停用访客账号
        ├─ 开关每日定时刷新
        └─ 将访客绑定到默认拒绝的实体白名单组
```

每天的刷新由集成在 Home Assistant 进程内调度，不要求管理页面保持打开。时间使用 Home Assistant 配置的本地时区。

## 从 0.1.0 升级

0.1.0 创建的用户属于 HA 默认普通用户组，因此升级到 0.2.0 后，集成会先停用 `mzuser`、撤销全部会话，再迁移到专用限制组。旧配置没有实体清单，升级后的初始结果是“拒绝全部实体”。

升级并重启后，以 Owner 打开 **设置 → 设备与服务 → Temporary Guest → 配置**，选择允许的实体并保存。确认管理卡显示的只读/可控制数量正确后，再把新密码交给访客。

## 移除集成

先在 **设置 → 设备与服务** 删除 Temporary Guest 配置项。集成会先停用临时用户并撤销其会话，然后删除自己保存的可读密码，但不会自动删除用户和专用限制组。保留限制组可以避免有人误启用残留账号后恢复全实体权限。需要彻底删除时，再由 Owner 前往 **设置 → 人员 → 用户** 删除该账号。

如果先从 HACS 删除代码，清理逻辑无法运行；这种情况下请手动停用或删除临时用户。

## 开发与验证

```powershell
python -m pip install -r requirements_test.txt
python -m compileall custom_components
ruff check .
pytest -q
node --check custom_components/temporary_guest/frontend/temporary-guest-card.js
```

实现依据 Home Assistant 2026.10.1 的以下接口：

- `HassAuthProvider.async_change_password(username, new_password)`；
- `AuthManager.async_update_user(user, is_active=...)`；
- `AuthManager.async_update_user(user, group_ids=...)`；
- `AuthManager.async_remove_refresh_token(refresh_token)`；
- `AuthStore._groups`、`AuthStore._async_schedule_save()` 与认证 Store 的即时持久化（HA 目前没有公开的自定义组创建接口）；
- `User.refresh_tokens`；
- `websocket_api.ActiveConnection.user.is_owner`。

## 已知限制

- 只支持内置 `homeassistant` 用户名/密码认证提供者；
- 只管理一个专用临时用户；
- 不能接管已有用户名，以避免误改真实账户；
- 实体白名单保护实体读取和控制，不是完整的页面 URL 或非实体元数据隔离；
- 自定义权限组需要使用 HA 2026.10.1 的内部认证存储接口；接口不兼容时集成会停用账号并拒绝加载，而不是退回普通用户的全实体权限；
- 因此升级 HA 大版本前必须等待本集成适配并测试，不能忽略版本锁定；
- 密码更新成功但私有存储写入失败时，集成会尝试回滚旧密码并报告失败。

## License

MIT
