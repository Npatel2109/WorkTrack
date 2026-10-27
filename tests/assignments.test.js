const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

test("engineer search matches names/email and excludes hidden selections from submission", () => {
  const search = { value: "", addEventListener: (_, callback) => { search.input = callback; } };
  const noMatches = { hidden: true };
  const engineers = ["Engineer One\nfirst@example.com", "Engineer Two\nsecond@example.com"].map(text => {
    const radio = { disabled: false, checked: false };
    return { textContent: text, hidden: false, radio, querySelector: () => radio };
  });
  engineers[0].radio.checked = true;
  const source = readFileSync(join(__dirname, "..", "geosite", "static", "js", "assignments.js"), "utf8");
  vm.runInNewContext(source, {
    document: {
      getElementById: id => id === "assignment-search" ? search : noMatches,
      querySelectorAll: () => engineers,
    },
  });
  search.value = " SECOND@EXAMPLE ";
  search.input();
  assert.equal(engineers[0].hidden, true);
  assert.equal(engineers[0].radio.disabled, true);
  assert.equal(engineers[0].radio.checked, false);
  assert.equal(engineers[1].hidden, false);
  assert.equal(noMatches.hidden, true);
  search.value = "not found";
  search.input();
  assert.equal(noMatches.hidden, false);
  search.value = "";
  search.input();
  assert.equal(engineers.every(item => !item.hidden && !item.radio.disabled), true);
  assert.equal(noMatches.hidden, true);
});
