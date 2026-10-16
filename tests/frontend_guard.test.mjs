import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const cardSource = await readFile(
  new URL("../custom_components/temporary_guest/frontend/temporary-guest-card.js", import.meta.url),
  "utf8",
);

const flush = async () => {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
};

function loadCard({ managed, pathname = "/lovelace" }) {
  const calls = [];
  const replacements = [];
  const listeners = new Map();
  const location = { pathname };
  const hass = {
    user: { id: managed ? "guest-id" : "owner-id" },
    async callWS(message) {
      calls.push({ ...message });
      return {
        managed,
        dashboard_path: managed ? "/dashboard-mzuser" : null,
      };
    },
  };
  const root = { hass };
  const elementRegistry = new Map();

  class MockHTMLElement {
    attachShadow() {
      this.shadowRoot = null;
    }
  }

  class MockCustomEvent {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  }

  const window = {
    location,
    history: {
      replaceState(_state, _title, path) {
        replacements.push(path);
        location.pathname = path;
      },
    },
    setTimeout,
    clearTimeout,
    addEventListener(type, listener) {
      const values = listeners.get(type) || new Set();
      values.add(listener);
      listeners.set(type, values);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event.type) || []) listener(event);
      return true;
    },
  };
  const customElements = {
    get(name) {
      return elementRegistry.get(name);
    },
    define(name, value) {
      elementRegistry.set(name, value);
    },
  };
  const context = vm.createContext({
    window,
    document: { querySelector: (selector) => (selector === "home-assistant" ? root : null) },
    customElements,
    HTMLElement: MockHTMLElement,
    CustomEvent: MockCustomEvent,
    navigator: { clipboard: { writeText: async () => {} } },
    console: { info() {} },
    setTimeout,
    clearTimeout,
  });

  vm.runInContext(cardSource, context, { filename: "temporary-guest-card.js" });
  return { calls, hass, location, replacements, window };
}

test("managed guest is confined to the exact Dashboard path and children", async () => {
  const page = loadCard({ managed: true });
  await flush();

  assert.equal(page.location.pathname, "/dashboard-mzuser");
  assert.deepEqual(page.replacements, ["/dashboard-mzuser"]);

  page.location.pathname = "/dashboard-mzuser/home";
  page.window.dispatchEvent({ type: "location-changed" });
  await flush();
  assert.deepEqual(page.replacements, ["/dashboard-mzuser"]);

  page.location.pathname = "/dashboard-mzuser-evil";
  page.window.dispatchEvent({ type: "popstate" });
  await flush();
  assert.equal(page.location.pathname, "/dashboard-mzuser");
  assert.deepEqual(page.replacements, ["/dashboard-mzuser", "/dashboard-mzuser"]);
  assert.ok(page.calls.length >= 3);
  assert.ok(page.calls.every((message) => JSON.stringify(message) === '{"type":"temporary_guest/ui_policy"}'));
});

test("other users receive no listener or redirect", async () => {
  const page = loadCard({ managed: false });
  await flush();

  assert.equal(page.location.pathname, "/lovelace");
  assert.deepEqual(page.replacements, []);
  page.location.pathname = "/another-dashboard";
  page.window.dispatchEvent({ type: "location-changed" });
  await flush();
  assert.deepEqual(page.replacements, []);
  assert.equal(page.calls.length, 1);
});
