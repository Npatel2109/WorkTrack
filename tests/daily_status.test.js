const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "geosite", "static", "js", "daily-status.js"), "utf8");

function setup() {
  const elements = {};
  function control(id) {
    return elements[id] = { value: "", disabled: false, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } };
  }
  elements["daily-status-form"] = {};
  const status = control("work-type");
  const site = control("daily-site");
  const submit = control("daily-submit");
  const reason = control("work-reason");
  const period = control("daily-leave-period");
  for (const [id, input] of Object.entries({
    "daily-site-fields": site, "daily-leave-fields": period,
    "daily-comment-fields": reason, "daily-submit-fields": submit,
  })) {
    elements[id] = { hidden: true, querySelectorAll: () => [input] };
  }
  vm.runInNewContext(source, { document: { getElementById: id => elements[id] } });
  return { elements, status, site, submit, reason, period, change(value) { status.value = value; status.listeners.change(); } };
}

test("daily status script is safe on other pages", () => {
  vm.runInNewContext(source, { document: { getElementById: () => null } });
});

test("site start appears only after On Site and an assigned site are selected", () => {
  const ui = setup();
  assert.equal(ui.submit.disabled, true);
  assert.equal(ui.site.disabled, true);
  assert.equal(ui.reason.disabled, true);
  ui.change("onsite");
  assert.equal(ui.elements["daily-site-fields"].hidden, false);
  assert.equal(ui.site.disabled, false);
  assert.equal(ui.submit.disabled, true);
  ui.site.value = "2";
  ui.site.listeners.change();
  assert.equal(ui.submit.disabled, false);
  assert.equal(ui.submit.textContent, "Start Site");
  assert.equal(ui.reason.disabled, true);
  assert.equal(ui.period.disabled, true);
  ui.site.value = "";
  ui.site.listeners.change();
  assert.equal(ui.submit.disabled, true);
});

test("non-site choices require the comment and only Leave enables duration", () => {
  const ui = setup();
  for (const status of ["wfh", "training", "travelling", "leave"]) {
    ui.change(status);
    assert.equal(ui.reason.disabled, false);
    assert.equal(ui.elements["daily-comment-fields"].hidden, false);
    assert.equal(ui.submit.disabled, false);
    assert.equal(ui.submit.textContent, "Submit for approval");
    assert.equal(ui.site.disabled, true);
    assert.equal(ui.period.disabled, status !== "leave");
  }
  ui.change("");
  assert.equal(ui.submit.disabled, true);
  assert.equal(ui.reason.disabled, true);
  assert.equal(ui.period.disabled, true);
});
