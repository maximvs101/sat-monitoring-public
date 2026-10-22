"use strict";

// store.js dans un contexte minimal (fetch, EventSource, minuteries simulés).
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadStore(fetchImpl) {
  const intervals = [];
  const ctx = vm.createContext({
    fetch: fetchImpl, console, Date, Map, Set, Promise, JSON, Math, Number, Object, Array, Error,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    setTimeout: () => 0, clearTimeout: () => {},
    EventSource: class { constructor() {} },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "store.js"), "utf8") + "\nthis.Store = Store;", ctx);
  return { Store: ctx.Store, intervals };
}

test("la minuterie d'une seconde démarre même si le premier chargement échoue", async () => {
  const { Store, intervals } = loadStore(async () => ({ ok: false, status: 500 }));
  await Store.start().catch(() => {});
  assert.strictEqual(intervals.filter((i) => i.ms === 1000).length, 1);
});
