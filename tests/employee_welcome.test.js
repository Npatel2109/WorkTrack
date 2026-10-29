const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "employee-welcome.js"), "utf8");

function setup({ saved, blocked = false, speech = true, throws = false, dialogSupport = true } = {}) {
  const elements = {};
  for (const id of ["employee-welcome", "robot-skip", "robot-play-voice", "robot-voice-status", "robot-welcome-title", "robot-welcome-message", "main-content"]) {
    elements[id] = {
      events: {}, textContent: "", dataset: { employee: "42", day: "2026-10-29" },
      addEventListener(name, fn) { this.events[name] = fn; },
      focus() { this.focused = true; },
    };
  }
  const dialog = elements["employee-welcome"];
  dialog.close = () => { dialog.open = false; dialog.events.close?.(); };
  if (dialogSupport) dialog.showModal = () => { dialog.open = true; };
  elements["robot-welcome-title"].textContent = "Hello, Engineer!";
  elements["robot-welcome-message"].textContent = "What are you doing today? Let's make it a great day!";
  const timers = new Map();
  const savedValues = [];
  const spoken = [];
  let cancelled = 0;
  const window = {
    events: {},
    addEventListener(name, fn) { this.events[name] = fn; },
    SpeechSynthesisUtterance: speech ? class { constructor(text) { this.text = text; } } : undefined,
    speechSynthesis: speech ? {
      speak(utterance) { if (throws) throw new Error("blocked"); spoken.push(utterance); },
      cancel() { cancelled += 1; },
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

test("daily greeting waits for the full spoken sentence then closes and restores focus", () => {
  const app = setup();
  assert.equal(app.dialog.open, true);
  assert.equal(app.elements["robot-skip"].focused, true);
  assert.deepEqual(app.savedValues, [["worktrack-welcome-42", "2026-10-29"]]);
  assert.match(app.spoken[0].text, /Hello, Engineer!/);
  assert.equal(app.spoken[0].lang, "en-IN");
  app.spoken[0].onstart();
  assert.equal(app.timers.has(4000), false);
  assert.equal(app.dialog.open, true);
  app.spoken[0].onend();
  assert.equal(app.dialog.open, false);
  assert.equal(app.cancelled(), 1);
  assert.equal(app.elements["main-content"].focused, true);
});

test("same-day greeting is suppressed and next-day greeting appears", () => {
  const same = setup({ saved: "2026-10-29" });
  assert.notEqual(same.dialog.open, true);
  assert.equal(same.spoken.length, 0);
  assert.equal(same.timers.size, 0);
  assert.equal(setup({ saved: "2026-10-28" }).dialog.open, true);
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
  assert.match(app.elements["robot-voice-status"].textContent, /blocks automatic audio/);
  app.elements["robot-play-voice"].events.click();
  assert.equal(app.spoken.length, 2);
  assert.equal(app.spoken[0].onend, null);
  app.spoken[1].onstart();
  assert.match(app.elements["robot-voice-status"].textContent, /dashboard is ready/);
  app.spoken[1].onerror();
  assert.match(app.elements["robot-voice-status"].textContent, /could not play/);
  app.timers.get(4000)();
  assert.equal(app.dialog.open, false);
});

test("blocked and stalled speech cannot leave the robot covering the dashboard", () => {
  const blocked = setup();
  blocked.timers.get(4000)();
  assert.equal(blocked.dialog.open, false);
  const stalled = setup();
  stalled.spoken[0].onstart();
  stalled.timers.get(20000)();
  assert.equal(stalled.dialog.open, false);
});

test("missing speech, failed speech, blocked storage and older dialogs never block dashboard access", () => {
  const noSpeech = setup({ speech: false });
  assert.equal(noSpeech.elements["robot-play-voice"].disabled, true);
  assert.match(noSpeech.elements["robot-voice-status"].textContent, /unavailable/);
  noSpeech.timers.get(4000)();
  assert.equal(noSpeech.dialog.open, false);
  assert.match(setup({ throws: true }).elements["robot-voice-status"].textContent, /could not play/);
  const blocked = setup({ blocked: true });
  assert.match(blocked.elements["robot-voice-status"].textContent, /cannot be saved/);
  blocked.elements["robot-skip"].events.click();
  assert.equal(blocked.dialog.open, false);
  assert.notEqual(setup({ dialogSupport: false }).dialog.open, true);
});
