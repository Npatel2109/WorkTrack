const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const workflow = readFileSync(join(__dirname, "..", "geosite", "static", "js", "workflow.js"), "utf8");
const tracking = readFileSync(join(__dirname, "..", "geosite", "static", "js", "location-tracking.js"), "utf8");

function setup({ flow = "checkin", secure = true, gpsError, networkError, track = false } = {}) {
  const events = {};
  const feedback = { className: "", textContent: "" };
  const button = {
    dataset: { flow, endpoint: "/capture" },
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  const status = { dataset: { endpoint: "/track" }, textContent: "" };
  const requests = [];
  const context = {
    document: {
      hidden: false,
      querySelector: () => ({ content: "csrf-token" }),
      getElementById: (id) => ({
        "verify-location": button, "gps-status": feedback,
        "visit-location-status": status,
      })[id] || null,
      addEventListener: (name, callback) => { events[name] = callback; },
    },
    navigator: {
      geolocation: {
        getCurrentPosition: (success, failure, options) => {
          assert.equal(options.maximumAge, 0);
          if (gpsError) failure({ code: gpsError });
          else success({ coords: { latitude: 0, longitude: 0.02 } });
        },
      },
    },
    window: {
      isSecureContext: secure,
      location: { assign: (url) => { events.redirect = url; } },
      setInterval: (callback, delay) => { events.tick = callback; assert.equal(delay, 60000); return 1; },
      clearInterval: () => {},
      addEventListener: (name, callback) => { events[name] = callback; },
    },
    fetch: async (url, options) => {
      requests.push({ url, ...options, payload: JSON.parse(options.body) });
      if (networkError) throw new Error("Server offline");
      return {
        ok: true, status: 200,
        json: async () => ({
          verified: true, distance: 2223.9, within_radius: false,
          location_source: gpsError || !secure ? "last_known" : "current", next: "/next",
        }),
      };
    },
  };
  vm.runInNewContext(track ? tracking : workflow, context);
  return { context, button, feedback, status, requests, events };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("outside-radius capture allows continuing and sends fresh GPS with CSRF", async () => {
  const page = setup();
  await page.events.click();
  await settle();
  assert.match(page.feedback.textContent, /Outside the reference radius; you can still continue/);
  assert.equal(page.button.textContent, "Continue");
  assert.equal(page.requests[0].payload.longitude, 0.02);
  assert.equal(page.requests[0].headers["X-CSRFToken"], "csrf-token");
  await page.events.click();
  assert.equal(page.events.redirect, "/next");
  assert.equal(page.requests.length, 1);
});

for (const [code, reason] of [[1, "permission_denied"], [2, "position_unavailable"], [3, "timeout"]]) {
  test(`stop falls back explicitly for ${reason}`, async () => {
    const page = setup({ flow: "checkout", gpsError: code });
    await page.events.click();
    await settle();
    assert.equal(page.requests[0].payload.location_unavailable, true);
    assert.equal(page.requests[0].payload.reason, reason);
    assert.match(page.feedback.textContent, /Visit stopped.*last captured location/);
    assert.equal(page.button.textContent, "Continue");
  });
}

test("starting never falls back when location permission is denied", async () => {
  const page = setup({ gpsError: 1 });
  await page.events.click();
  assert.equal(page.requests.length, 0);
  assert.match(page.feedback.textContent, /permission is off/);
  assert.equal(page.button.textContent, "Try again");
});

test("insecure context and unsupported GPS can stop using the stored location", async () => {
  for (const unsupported of [false, true]) {
    const page = setup({ flow: "checkout", secure: unsupported });
    if (unsupported) delete page.context.navigator.geolocation;
    await page.events.click();
    await settle();
    assert.equal(page.requests[0].payload.reason, unsupported ? "unsupported" : "insecure_context");
    assert.equal(page.button.textContent, "Continue");
  }
});

test("network failures stay visible and never claim a successful stop", async () => {
  const page = setup({ flow: "checkout", networkError: true });
  await page.events.click();
  await settle();
  assert.match(page.feedback.textContent, /Server offline/);
  assert.equal(page.button.textContent, "Try again");
  assert.equal(page.requests.length, 1);
});

test("active tracking sends fresh locations only while the page is visible", async () => {
  const page = setup({ track: true });
  await settle();
  assert.equal(page.requests[0].url, "/track");
  assert.match(page.status.textContent, /Last location saved/);
  page.context.document.hidden = true;
  page.events.tick();
  assert.equal(page.requests.length, 1);
  page.context.document.hidden = false;
  page.events.visibilitychange();
  await settle();
  assert.equal(page.requests.length, 2);
});

test("tracking permission failure preserves the previous fix and displays a warning", async () => {
  const page = setup({ track: true, gpsError: 1 });
  await settle();
  assert.equal(page.requests.length, 0);
  assert.match(page.status.textContent, /last captured location is retained/);
});

test("tracking stops after the server says the visit has ended", async () => {
  const page = setup({ track: true, gpsError: 1 });
  page.context.navigator.geolocation.getCurrentPosition = (success) => success({ coords: { latitude: 1, longitude: 1 } });
  page.context.fetch = async () => ({ ok: false, status: 409, json: async () => ({ active: false }) });
  page.events.tick();
  await settle();
  assert.match(page.status.textContent, /visit has stopped/);
  page.context.navigator.geolocation.getCurrentPosition = () => assert.fail("Stopped visit must not request GPS");
  page.events.tick();
});
