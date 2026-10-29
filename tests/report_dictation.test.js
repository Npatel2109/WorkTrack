const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "report-dictation.js"), "utf8");

function setup({ browser = true, native = false, secure = true, missing = false, throws = false } = {}) {
  const element = () => ({
    value: "", disabled: false, textContent: "", attributes: {}, events: {},
    addEventListener(name, callback) { this.events[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    dispatchEvent(event) { this.events[event.type]?.(event); },
  });
  const field = element();
  const button = element();
  const status = element();
  const form = element();
  const instances = [];
  class Recognition {
    constructor() { instances.push(this); }
    start() { if (throws) throw new Error("Cannot start"); }
    stop() { this.stopped = true; }
    abort() { this.aborted = true; }
  }
  const window = {
    isSecureContext: secure, worktrackNativeSpeech: native, events: {}, location: {},
    addEventListener(name, callback) { this.events[name] = callback; },
    SpeechRecognition: browser ? Recognition : undefined,
  };
  const elements = { "activity-detail": field, "report-dictate": button, "dictation-status": status, "site-report-form": form };
  vm.runInNewContext(source, {
    window, document: { getElementById: (id) => missing ? null : elements[id] },
    Event: class { constructor(type) { this.type = type; } },
    console: { warn() {} },
  });
  const transcript = (text, final = true) => {
    const result = [{ transcript: text }];
    result.isFinal = final;
    instances.at(-1).onresult({ resultIndex: 0, results: [result] });
  };
  return { field, button, status, form, window, instances, transcript };
}

test("English dictation appends text without replacing manual edits or submitting", () => {
  const app = setup();
  assert.equal(app.button.disabled, false);
  app.field.value = "Manual inspection";
  app.button.events.click();
  const recognition = app.instances[0];
  assert.equal(recognition.lang, "en-IN");
  assert.equal(recognition.continuous, false);
  assert.equal(recognition.interimResults, false);
  assert.equal(app.button.attributes["aria-pressed"], "true");
  let inputEvents = 0;
  app.field.events.input = () => { inputEvents += 1; };
  app.transcript("unfinished", false);
  assert.equal(app.field.value, "Manual inspection");
  app.field.value += " and testing";
  app.transcript("Replaced the fuse.");
  recognition.onend();
  assert.equal(app.field.value, "Manual inspection and testing\nReplaced the fuse.");
  assert.equal(inputEvents, 1);
  assert.equal(app.button.attributes["aria-pressed"], "false");
  assert.match(app.status.textContent, /Review and edit/);
  app.field.value = "Edited final report";
  let prevented = false;
  app.form.events.submit({ preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(app.field.value, "Edited final report");
});

test("stop finalizes speech and blocks submission until recognition has ended", () => {
  const app = setup();
  app.button.events.click();
  let prevented = false;
  app.form.events.submit({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  app.button.events.click();
  assert.equal(app.instances[0].stopped, true);
  app.transcript("Completed testing");
  app.instances[0].onend();
  assert.equal(app.field.value, "Completed testing");
  assert.equal(app.button.disabled, false);
});

test("recognition errors are explicit, preserve text and permit retry", () => {
  for (const error of ["not-allowed", "service-not-allowed", "audio-capture", "network", "no-speech", "aborted", "language-not-supported", "unknown"]) {
    const app = setup();
    app.field.value = "Existing text";
    app.button.events.click();
    app.instances[0].onerror({ error });
    const message = app.status.textContent;
    app.instances[0].onend();
    assert.equal(app.status.textContent, message);
    assert.match(message, /normally|unchanged/);
    assert.equal(app.field.value, "Existing text");
    assert.equal(app.button.disabled, false);
    app.button.events.click();
    assert.equal(app.instances.length, 2);
  }
});

test("missing speech, start failure and unsupported browsers retain normal input", () => {
  const silent = setup();
  silent.button.events.click();
  silent.instances[0].onend();
  assert.match(silent.status.textContent, /No speech/);
  const failed = setup({ throws: true });
  failed.button.events.click();
  assert.equal(failed.button.attributes["aria-pressed"], "false");
  assert.match(failed.status.textContent, /could not start/);
  for (const options of [{ browser: false }, { secure: false }]) {
    const app = setup(options);
    assert.equal(app.button.disabled, true);
    assert.match(app.status.textContent, /unavailable/);
    assert.equal(app.field.disabled, false);
  }
  assert.doesNotThrow(() => setup({ missing: true }));
});

test("native Android speech enables after page load and appends safely only once", () => {
  const app = setup({ browser: false });
  app.window.worktrackNativeSpeech = true;
  app.window.events["worktrack-speech-ready"]();
  assert.equal(app.button.disabled, false);
  app.field.value = "Typed text ";
  app.button.events.click();
  assert.equal(app.window.location.href, "worktrack-voice://start");
  assert.equal(app.button.disabled, true);
  const event = { detail: { text: 'Checked <script>alert("test")</script>', error: "" } };
  app.window.events["worktrack-speech-result"](event);
  app.window.events["worktrack-speech-result"](event);
  assert.equal(app.field.value, 'Typed text Checked <script>alert("test")</script>');
  assert.equal(app.button.disabled, false);
  assert.equal(app.instances.length, 0);
});

test("native cancellation, absent service and late results preserve the report", () => {
  const app = setup({ native: true });
  app.field.value = "Manual report";
  app.window.events["worktrack-speech-result"]({ detail: { text: "Unrequested" } });
  assert.equal(app.field.value, "Manual report");
  for (const error of ["Voice typing cancelled.", "English voice typing is unavailable."]) {
    app.button.events.click();
    app.window.events["worktrack-speech-result"]({ detail: { error } });
    assert.equal(app.status.textContent, error);
    assert.equal(app.field.value, "Manual report");
    assert.equal(app.button.disabled, false);
  }
  app.button.events.click();
  app.window.events.pagehide();
  app.window.events["worktrack-speech-result"]({ detail: { text: "Late result" } });
  assert.equal(app.field.value, "Manual report");
  app.window.events.pageshow();
  assert.equal(app.button.disabled, false);
});

test("leaving the page aborts browser speech and ignores late transcripts", () => {
  const app = setup();
  app.button.events.click();
  app.window.events.pagehide();
  assert.equal(app.instances[0].aborted, true);
  app.transcript("Must not be inserted");
  assert.equal(app.field.value, "");
  app.window.events.pageshow();
  assert.equal(app.button.attributes["aria-pressed"], "false");
});
