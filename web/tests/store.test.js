"use strict";

// store.js dans un contexte minimal (fetch, EventSource, minuteries simulés).
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadStore(fetchImpl) {
  const intervals = [];
  const sources = [];
  const ctx = vm.createContext({
    fetch: fetchImpl, console, Date, Map, Set, Promise, JSON, Math, Number, Object, Array, Error,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    setTimeout: () => 0, clearTimeout: () => {},
    EventSource: class { constructor() { sources.push(this); } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "store.js"), "utf8") + "\nthis.Store = Store;", ctx);
  return { Store: ctx.Store, intervals, sources };
}

test("la minuterie d'une seconde démarre même si le premier chargement échoue", async () => {
  const { Store, intervals } = loadStore(async () => ({ ok: false, status: 500 }));
  await Store.start().catch(() => {});
  assert.strictEqual(intervals.filter((i) => i.ms === 1000).length, 1);
});

test("les mesures du direct ne redessinent qu'une fois par seconde (au plus)", async () => {
  const { Store, intervals, sources } = loadStore(async () => ({ ok: false, status: 500 }));
  await Store.start().catch(() => {});
  const kinds = [];
  Store.subscribe((k) => kinds.push(k));
  for (let i = 0; i < 20; i++) {
    sources[0].onmessage({ data: JSON.stringify({ type: "markers", ts: 1000 + i, markers: {} }) });
  }
  assert.deepStrictEqual(kinds.filter((k) => k !== "reload"), []);
  intervals[0].fn();
  assert.deepStrictEqual(kinds.filter((k) => k !== "reload"), ["tick"]);
});
