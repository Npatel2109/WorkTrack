const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

test("login password toggles both ways without changing its value or submitting", () => {
  const events = {};
  const attributes = {};
  const password = { type: "password", value: "typed-test-password" };
  const toggle = {
    textContent: "Show",
    addEventListener: (name, callback) => { events[name] = callback; },
    setAttribute: (name, value) => { attributes[name] = value; },
  };
  const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "app.js"), "utf8");
  vm.runInNewContext(source, {
    document: {
      getElementById: (id) => ({ "toggle-password": toggle, password })[id] || null,
    },
    localStorage: { getItem: () => null },
    window: {},
  });
  events.click();
  assert.equal(password.type, "text");
  assert.equal(toggle.textContent, "Hide");
  assert.equal(attributes["aria-label"], "Hide password");
  assert.equal(attributes["aria-pressed"], "true");
  assert.equal(password.value, "typed-test-password");
  events.click();
  assert.equal(password.type, "password");
  assert.equal(toggle.textContent, "Show");
  assert.equal(attributes["aria-label"], "Show password");
  assert.equal(attributes["aria-pressed"], "false");
  assert.equal(password.value, "typed-test-password");
});

test("pages without a password toggle still initialize normally", () => {
  const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "app.js"), "utf8");
  const window = {};
  vm.runInNewContext(source, {
    document: { getElementById: () => null },
    localStorage: { getItem: () => null },
    window,
  });
  assert.equal(typeof window.GeoSite.startLiveStatusUpdates, "function");
});
