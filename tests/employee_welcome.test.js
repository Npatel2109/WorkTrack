const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "employee-welcome.js"), "utf8");

function setup({ saved, blocked = false, speech = true, throws = false, dialogSupport = true, thankYou = false, native = false } = {}) {
  const elements = {};
  for (const id of ["employee-welcome", "robot-skip", "robot-play-voice", "robot-voice-status", "robot-welcome-title", "robot-welcome-message", "main-content"]) {
    elements[id] = {
      events: {}, textContent: "", dataset: { employee: "42", day: "2026-10-29" },
      addEventListener(name, fn) { this.events[name] = fn; },
      focus() { this.focused = true; },
    };
  }
  const dialog = elements["employee-welcome"];
  dialog.dataset.kind = thankYou ? "thank-you" : "welcome";
  dialog.close = () => { dialog.open = false; dialog.events.close?.(); };
  if (dialogSupport) dialog.showModal = () => { dialog.open = true; };
  elements["robot-welcome-title"].textContent = thankYou ? "Thank you, Engineer!" : "Hello, Engineer!";
  elements["robot-welcome-message"].textContent = thankYou
    ? "Your site report has been submitted. Thank you for your hard work!"
    : "What are you doing today? Let's make it a great day!";
  const timers = new Map();
  const savedValues = [];
  const spoken = [];
  let cancelled = 0;
  const window = {
    location: { href: "" },
    worktrackNativeRobot: native,
    events: {},
    addEventListener(name, fn) { this.events[name] = fn; },
    SpeechSynthesisUtterance: speech ? class { constructor(text) { this.text = text; } } : undefined,
    speechSynthesis: speech ? {
      speak(utterance) { if (throws) throw new Error("blocked"); spoken.push(utterance); },
      cancel() { cancelled += 1; },
      resume() {},
      getVoices() { return [{ lang: "en-US" }, { lang: "en-IN" }]; },
    } : undefined,
  };
  vm.runInNewContext(source, {
    document: { getElementById: (id) => elements[id], activeElement: null },
    window, localStorage: {
      getItem() { if (blocked) throw new Error("Storage blocked"); return saved; },
      setItem(key, value) { savedValues.push([key, value]); },
    },
    setTimeout(fn, ms) { timers.set(ms, fn); return ms; },
    clearTimeout(id) { timers.delete(id); },
    console: { warn() {} },
  });
  return { elements, dialog, window, timers, savedValues, spoken, cancelled: () => cancelled };
}

test("login greeting waits for the full spoken sentence then closes and restores focus", () => {
  const app = setup();
  assert.equal(app.dialog.open, true);
  assert.equal(app.elements["robot-play-voice"].focused, true);
  assert.deepEqual(app.savedValues, []);
  assert.match(app.spoken[0].text, /Hello, Engineer!/);
  assert.equal(app.spoken[0].lang, "en-IN");
  assert.equal(app.spoken[0].voice.lang, "en-IN");
  assert.equal(app.spoken[0].volume, 1);
  app.spoken[0].onstart();
  assert.equal(app.timers.has(4000), false);
  assert.equal(app.dialog.open, true);
  app.spoken[0].onend();
  assert.equal(app.dialog.open, false);
  assert.equal(app.cancelled(), 1);
  assert.equal(app.elements["main-content"].focused, true);
});

test("every login speaks even if an old same-day preference exists", () => {
  const same = setup({ saved: "2026-10-29" });
  assert.equal(same.dialog.open, true);
  assert.equal(same.spoken.length, 1);
  assert.deepEqual(same.savedValues, []);
  assert.equal(setup({ saved: "2026-10-28" }).dialog.open, true);
});

test("each report thank-you speaks in full despite the daily welcome and does not change its preference", () => {
  for (let visit = 0; visit < 2; visit += 1) {
    const app = setup({ saved: "2026-10-29", thankYou: true, blocked: true });
    assert.equal(app.dialog.open, true);
    assert.deepEqual(app.savedValues, []);
    assert.match(app.spoken[0].text, /Thank you, Engineer! Your site report has been submitted/);
    assert.doesNotMatch(app.elements["robot-voice-status"].textContent, /cannot be saved/);
    app.spoken[0].onstart();
    assert.equal(app.timers.has(4000), false);
    assert.equal(app.dialog.open, true);
    app.spoken[0].onend();
    assert.equal(app.dialog.open, false);
    assert.equal(app.elements["main-content"].focused, true);
  }
});

test("report thank-you permits skipping the whole robot when audio is unavailable", () => {
  const skipped = setup({ thankYou: true });
  skipped.elements["robot-skip"].events.click();
  assert.equal(skipped.dialog.open, false);
  assert.equal(skipped.cancelled(), 1);
  const silent = setup({ thankYou: true, speech: false });
  assert.equal(silent.timers.size, 0);
  assert.equal(silent.dialog.open, true);
  silent.elements["robot-skip"].events.click();
  assert.equal(silent.dialog.open, false);
});

test("skip, Escape and navigation close the greeting and stop speech", () => {
  for (const action of ["skip", "escape", "pagehide"]) {
    const app = setup();
    if (action === "skip") app.elements["robot-skip"].events.click();
    if (action === "escape") app.dialog.events.cancel({ preventDefault() {} });
    if (action === "pagehide") app.window.events.pagehide();
    assert.equal(app.dialog.open, false);
    assert.equal(app.timers.size, 0);
    assert.equal(app.cancelled(), 1);
    app.elements["robot-play-voice"].events.click();
    assert.equal(app.spoken.length, 1);
  }
});

test("blocked audio can be retried and completion opens the dashboard", () => {
  const app = setup();
  app.timers.get(700)();
  assert.match(app.elements["robot-voice-status"].textContent, /allow sound on mobile/);
  app.elements["robot-play-voice"].events.click();
  assert.equal(app.spoken.length, 2);
  assert.equal(app.spoken[0].onend, null);
  app.spoken[1].onstart();
  assert.match(app.elements["robot-voice-status"].textContent, /Robot is speaking/);
  app.spoken[1].onerror();
  assert.match(app.elements["robot-voice-status"].textContent, /could not play/);
  assert.equal(app.dialog.open, true);
  assert.equal(app.timers.size, 0);
  app.elements["robot-play-voice"].events.click();
  app.spoken[2].onstart();
  app.spoken[2].onend();
  assert.equal(app.dialog.open, false);
});

test("blocked and stalled speech require retry or explicit skip, never silent dismissal", () => {
  const blocked = setup();
  blocked.timers.get(20000)();
  assert.equal(blocked.dialog.open, true);
  assert.match(blocked.elements["robot-voice-status"].textContent, /could not play/);
  blocked.elements["robot-skip"].events.click();
  assert.equal(blocked.dialog.open, false);
  const stalled = setup();
  stalled.spoken[0].onstart();
  stalled.timers.get(20000)();
  assert.equal(stalled.dialog.open, true);
  stalled.elements["robot-skip"].events.click();
  assert.equal(stalled.dialog.open, false);
});

test("missing speech, failed speech, blocked storage and older dialogs never block dashboard access", () => {
  const noSpeech = setup({ speech: false });
  assert.notEqual(noSpeech.elements["robot-play-voice"].disabled, true);
  assert.match(noSpeech.elements["robot-voice-status"].textContent, /unavailable/);
  assert.equal(noSpeech.timers.size, 0);
  noSpeech.elements["robot-skip"].events.click();
  assert.equal(noSpeech.dialog.open, false);
  assert.match(setup({ throws: true }).elements["robot-voice-status"].textContent, /could not play/);
  const blocked = setup({ blocked: true });
  assert.doesNotMatch(blocked.elements["robot-voice-status"].textContent, /cannot be saved/);
  blocked.elements["robot-skip"].events.click();
  assert.equal(blocked.dialog.open, false);
  assert.notEqual(setup({ dialogSupport: false }).dialog.open, true);
});

test("Android native voice replaces missing WebView speech and closes only after completion", () => {
  const app = setup({ native: true, speech: false });
  assert.match(app.window.location.href, /^worktrack-robot:\/\/speak\?id=1&text=Hello/);
  assert.equal(app.spoken.length, 0);
  app.window.events["worktrack-robot-result"]({ detail: { id: "1", state: "start" } });
  assert.match(app.elements["robot-voice-status"].textContent, /Robot is speaking/);
  app.window.events["worktrack-robot-result"]({ detail: { id: "1", state: "done" } });
  assert.equal(app.dialog.open, false);
  assert.equal(app.window.location.href, "worktrack-robot://stop");
});

test("Android readiness upgrades browser speech and ignores old speech callbacks after retry or skip", () => {
  const app = setup();
  const oldEnd = app.spoken[0].onend;
  app.window.worktrackNativeRobot = true;
  app.window.events["worktrack-robot-ready"]();
  assert.equal(app.spoken[0].onend, null);
  app.window.events["worktrack-robot-result"]({ detail: { id: "1", state: "error" } });
  assert.equal(app.dialog.open, true);
  assert.match(app.elements["robot-voice-status"].textContent, /could not play/);
  app.elements["robot-play-voice"].events.click();
  app.window.events["worktrack-robot-result"]({ detail: { id: "1", state: "done" } });
  assert.equal(app.dialog.open, true);
  app.elements["robot-skip"].events.click();
  app.window.events["worktrack-robot-result"]({ detail: { id: "2", state: "start" } });
  oldEnd();
  assert.equal(app.dialog.open, false);
  assert.equal(app.timers.size, 0);
});
