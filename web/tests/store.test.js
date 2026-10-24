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

// Service simulé : chaque requête attend que le test la libère (chargement en cours).
function fakeService() {
  const calls = [];
  const counts = { status: 0 };
  const fetchImpl = (url) => new Promise((resolve) => {
    if (url.startsWith("/api/status")) counts.status++;
    calls.push({ url, resolve: (body) => resolve({ ok: true, status: 200, json: async () => body }) });
  });
  const answer = async (events = []) => {
    // Répond aux requêtes en attente, dans l'ordre : statut, puis séries et événements.
    while (calls.length) {
      const c = calls.shift();
      if (c.url.startsWith("/api/status")) {
        c.resolve({ now: 100_000, config: { live_window_min: 10, max_points: 5000, event_padding_s: 30 }, carriers: [],
          marker_map: { noise: [], if_warning: false }, irds: {}, fsh: { state: "up" }, devices: {} });
      } else if (c.url.startsWith("/api/series")) {
        c.resolve({ fsh: {}, irds: {}, names: {} });
      } else {
        c.resolve(events);
      }
      await new Promise((r) => setImmediate(r));
    }
  };
  return { fetchImpl, calls, answer, counts };
}

test("une alerte fermée pendant un rechargement reste fermée", async () => {
  const srv = fakeService();
  const { Store, sources } = loadStore(srv.fetchImpl);
  const started = Store.start();
  await srv.answer([{ id: 5, kind: "lock_lost", ts_start: 90_000, ts_end: null }]);
  await started;
  Store.reload();  // rechargement (marqueur déplacé…) : instantané des événements pris avant la fermeture
  await new Promise((r) => setImmediate(r));
  sources[0].onmessage({ data: JSON.stringify({ type: "event", id: 5, kind: "lock_lost", ts_start: 90_000, ts_end: 95_000 }) });
  await srv.answer([{ id: 5, kind: "lock_lost", ts_start: 90_000, ts_end: null }]);
  assert.strictEqual(Store.state.events.get(5).ts_end, 95_000);
});

test("un rechargement demandé pendant un autre n'est pas perdu", async () => {
  const srv = fakeService();
  const { Store } = loadStore(srv.fetchImpl);
  const started = Store.start();
  await srv.answer();
  await started;
  Store.reload();
  await new Promise((r) => setImmediate(r));
  Store.reload();  // porteuse apparue pendant le premier rechargement
  await srv.answer();
  await srv.answer();
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(srv.calls.length, 0);  // plus rien en attente
  assert.strictEqual(srv.counts.status, 3);  // démarrage, rechargement, rechargement redemandé
});

test("fenêtre du direct longue : jamais de points regroupés mêlés aux points bruts", async () => {
  const urls = [];
  const fetchImpl = async (url) => {
    urls.push(url);
    const body = url.startsWith("/api/status")
      ? { now: 100_000_000, config: { live_window_min: 30, max_points: 5000, event_padding_s: 30 }, carriers: [],
        marker_map: { noise: [], if_warning: false }, irds: {}, fsh: { state: "up" }, devices: {} }
      : url.startsWith("/api/series") ? { fsh: {}, irds: {}, names: {} } : [];
    return { ok: true, status: 200, json: async () => body };
  };
  const { Store } = loadStore(fetchImpl);
  await Store.start();
  const series = urls.find((u) => u.startsWith("/api/series"));
  const points = Number(new URL(series, "http://x").searchParams.get("max_points"));
  assert.ok(points >= 30 * 60 * 4, `max_points=${points}`);  // FSH4 à 4 Hz sur 30 min
});

test("flux en direct fermé (réponse en erreur) : la page se reconnecte seule", async () => {
  const timers = [];
  const sources = [];
  const ctx = vm.createContext({
    fetch: async () => ({ ok: false, status: 500 }), console, Date, Map, Set, Promise, JSON, Math, Number, Object, Array, Error,
    setInterval: () => 1, setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {},
    EventSource: class { constructor() { this.readyState = 0; sources.push(this); } close() { this.readyState = 2; } },
  });
  ctx.EventSource.CLOSED = 2;
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "store.js"), "utf8") + "\nthis.Store = Store;", ctx);
  await ctx.Store.start().catch(() => {});
  sources[0].readyState = 2;  // le navigateur abandonne après une réponse HTTP en erreur
  sources[0].onerror();
  assert.ok(timers.length >= 1, "reconnexion programmée");
  timers[timers.length - 1].fn();
  assert.strictEqual(sources.length, 2);
});
