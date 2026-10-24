"use strict";

// Tests de la logique de la page (sans navigateur) : node --test web/tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../core.js");

const NOW = 1_800_000_000_000;

test("adresse d'une porteuse dont la clé contient « ? », avec période et IRD ciblé", () => {
  const route = core.parseRoute("#/porteuse/125830-%3F?p=24h&ird=S1");
  assert.deepEqual(route, { page: "carrier", key: "125830-?", period: "24h", ird: "S1" });
  assert.equal(core.routeHref(route), "#/porteuse/125830-%3F?p=24h&ird=S1");
});

test("adresses des autres pages", () => {
  assert.deepEqual(core.parseRoute(""), { page: "overview" });
  assert.deepEqual(core.parseRoute("#/"), { page: "overview" });
  assert.deepEqual(core.parseRoute("#/journal?p=7j&src=ird%3AS1&open=1"),
    { page: "journal", period: "7j", src: "ird:S1", open: "1" });
  assert.deepEqual(core.parseRoute("#/fsh4"), { page: "fsh" });
  assert.deepEqual(core.parseRoute("#/reglages/seuils"), { page: "settings", tab: "seuils" });
  assert.deepEqual(core.parseRoute("#/reglages"), { page: "settings", tab: "appareils" });
  assert.deepEqual(core.parseRoute("#/reglages/autre"), { page: "notfound" });
  assert.deepEqual(core.parseRoute("#/nimporte"), { page: "notfound" });
  assert.equal(core.routeHref({ page: "overview" }), "#/");
  assert.equal(core.routeHref({ page: "settings", tab: "porteuses" }), "#/reglages/porteuses");
  assert.equal(core.routeHref({ page: "fsh", period: "1h" }), "#/fsh4?p=1h");
});

test("bornes d'une période", () => {
  assert.deepEqual(core.periodRange("live", NOW, 10), [NOW - 600_000, NOW]);
  assert.deepEqual(core.periodRange(undefined, NOW, 10), [NOW - 600_000, NOW]);
  assert.deepEqual(core.periodRange("1h", NOW, 10), [NOW - 3_600_000, NOW]);
  assert.deepEqual(core.periodRange("7j", NOW, 10), [NOW - 7 * 86_400_000, NOW]);
  assert.deepEqual(core.periodRange("1700000000000-1700003600000", NOW, 10), [1700000000000, 1700003600000]);
  assert.deepEqual(core.periodRange("1700003600000-1700000000000", NOW, 10), [NOW - 600_000, NOW]);  // inversée
  assert.deepEqual(core.periodRange("n'importe", NOW, 10), [NOW - 600_000, NOW]);
  assert.equal(core.isLive("live"), true);
  assert.equal(core.isLive(undefined), true);
  assert.equal(core.isLive("24h"), false);
});

test("libellé d'une période", () => {
  assert.equal(core.periodLabel("live", 10), "Direct 10 min");
  assert.equal(core.periodLabel("7j", 10), "7 j");
  assert.match(core.periodLabel("1700000000000-1700003600000", 10), /^du .+ au .+$/);
});

const CARRIERS = [
  { key: "125830-V", rf_khz: 12_583_023, polarization: "vertical", symbol_rate: 7_200_000 },
  { key: "112208-?", rf_khz: 11_220_806, polarization: null, symbol_rate: 24_760_000 },
];

test("porteuse retrouvée par sa clé, sinon par fréquence et polarisation", () => {
  assert.equal(core.matchCarrier(CARRIERS, { key: "125830-V" }), CARRIERS[0]);
  // Clé d'une autre période (fréquence arrondie différemment), même porteuse.
  assert.equal(core.matchCarrier(CARRIERS, { key: "125831-V" }), CARRIERS[0]);
  assert.equal(core.matchCarrier(CARRIERS, { rf_khz: 12_583_070, polarization: "vertical", symbol_rate: 7_200_000 }), CARRIERS[0]);
  assert.equal(core.matchCarrier(CARRIERS, { rf_khz: 12_583_023, polarization: "horizontal" }), null);
  assert.equal(core.matchCarrier(CARRIERS, { rf_khz: 12_587_023, polarization: "vertical", symbol_rate: 7_200_000 }), null);
  // Polarisation inconnue d'un côté : compatible.
  assert.equal(core.matchCarrier(CARRIERS, { key: "112208-H" }), CARRIERS[1]);
  assert.equal(core.matchCarrier([], { key: "125830-V" }), null);
});

test("fréquence et polarisation relues dans une clé", () => {
  assert.deepEqual(core.carrierKeyRef("125830-?"), { rf_khz: 12_583_000, polarization: null });
  assert.deepEqual(core.carrierKeyRef("125830-V"), { rf_khz: 12_583_000, polarization: "vertical" });
  assert.deepEqual(core.carrierKeyRef("117000-H"), { rf_khz: 11_700_000, polarization: "horizontal" });
  assert.equal(core.carrierKeyRef("pas une clé"), null);
});

test("résumé d'une période pour un IRD", () => {
  const series = { ts: [0, 1000, 2000, 3000, 4000], margin_db: [7, 6, 0, 0, 7], state: [0, 0, 3, 3, 0] };
  const summary = core.periodSummary(series, [{ id: 1 }, { id: 2 }]);
  assert.equal(summary.margin_min, 0);
  assert.equal(summary.state_ms[3], 2000);
  assert.equal(summary.state_ms[0], 2000);
  assert.equal(summary.alerts, 2);
  assert.deepEqual(core.periodSummary({ ts: [], margin_db: [], state: [] }, []),
    { margin_min: null, state_ms: { 0: 0, 1: 0, 2: 0, 3: 0 }, alerts: 0 });
  // Historique agrégé (un point par minute) : chaque point compte pour son intervalle.
  const agg = core.periodSummary({ ts: [0, 60_000, 120_000], margin_db: [5, 5, 5], state: [1, 1, 0] }, []);
  assert.equal(agg.state_ms[1], 120_000);
});

test("état d'un IRD en clair", () => {
  const ok = { locked: true, ts_locked: true, video_active: true, scrambled: true, descrambled: true,
    bitrate_kbps: 15000, audio_configured: 5, audio_active: 5, ber: 0, margin_db: 6.5, state: 0 };
  assert.deepEqual(core.irdStatus(ok, false, 0), ["OK", "", 0]);
  assert.deepEqual(core.irdStatus({ ...ok, locked: false, state: 3 }, false, 0), ["LOCK PERDU", "plus de signal démodulé", 3]);
  assert.deepEqual(core.irdStatus({ ...ok, video_active: false, state: 2 }, false, 0)[0], "PAS D'IMAGE");
  assert.deepEqual(core.irdStatus(ok, false, 7), ["MARGE BASSE", "sous le minimum de 7,0 dB", 1]);
  assert.deepEqual(core.irdStatus(ok, true, 0), ["INJOIGNABLE", "l'IRD ne répond plus", "down"]);
  assert.deepEqual(core.irdStatus(undefined, false, 0), ["EN ATTENTE", "pas encore de mesure", "idle"]);
});

test("où mène le clic sur un événement", () => {
  const T = 1_700_000_000_000;
  const carriersAt = [{ key: "125830-V", irds: ["S1"], periods: { S1: [[T, T + 50_000]] } },
    { key: "112208-?", irds: ["S1"], periods: { S1: [[T + 60_000, T + 90_000]] } }];
  const ev = { source: "ird:S1", ts_start: T + 20_000, ts_end: T + 25_000 };
  assert.deepEqual(core.eventTarget(ev, carriersAt, NOW, 30_000),
    { page: "carrier", key: "125830-V", period: `${T - 10_000}-${T + 55_000}` });
  // IRD re-réglé : la porteuse de l'époque, pas l'actuelle ; en cours : jusqu'à maintenant.
  const open = core.eventTarget({ ...ev, ts_start: T + 70_000, ts_end: null }, carriersAt, T + 100_000, 30_000);
  assert.deepEqual(open, { page: "carrier", key: "112208-?", period: `${T + 40_000}-${T + 130_000}` });
  assert.deepEqual(core.eventTarget({ source: "fsh", ts_start: T + 20_000, ts_end: null }, carriersAt, T + 100_000, 30_000),
    { page: "fsh", period: `${T - 10_000}-${T + 130_000}` });
  assert.equal(core.eventTarget({ source: "porteuse:125830-V", ts_start: T + 20_000, ts_end: T + 21_000 }, carriersAt, NOW, 30_000).key,
    "125830-V");
  assert.equal(core.eventTarget({ source: "ird:S9", ts_start: T + 20_000, ts_end: T + 21_000 }, carriersAt, NOW, 30_000), null);
});

test("formats à la française", () => {
  assert.equal(core.fmt(6.54, 1, "dB"), "6,5 dB");
  assert.equal(core.fmt(null, 1, "dB"), "—");
  assert.equal(core.fmtBer(0), "< 1e-8");
});

test("périodes : clés héritées refusées, isFixedPeriod", () => {
  const { isLive, periodRange, isFixedPeriod } = core;
  for (const p of ["constructor", "toString", "__proto__"]) {
    assert.strictEqual(isLive(p), true, p);
    assert.deepStrictEqual(periodRange(p, NOW, 10), [NOW - 600_000, NOW], p);
    assert.strictEqual(isFixedPeriod(p), false, p);
  }
  assert.strictEqual(isFixedPeriod("7j"), true);
  assert.strictEqual(isFixedPeriod("1-2"), false);
  assert.strictEqual(isFixedPeriod(undefined), false);
});

test("résumé d'une période agrégée : durées réelles de chaque état (pas l'intervalle entier)", () => {
  // 30 jours : intervalles de 29 min ; une perte de lock de 2 s ne compte que 2 s.
  const series = { ts: [0, 60_000], margin_db: [5, 6], state: [3, 0], state_ms: [[58_000, 0, 0, 2_000], [60_000, 0, 0, 0]] };
  assert.deepEqual(core.periodSummary(series, []).state_ms, { 0: 118_000, 1: 0, 2: 0, 3: 2_000 });
});

test("IRD sans nouvelles : ni « OK » ni valeurs figées quand le direct est coupé", () => {
  const ok = { locked: true, ts_locked: true, video_active: true, margin_db: 7, state: 0 };
  assert.deepEqual(core.irdStatus(ok, false, 0, true), ["SANS NOUVELLES", "aucune mesure reçue récemment", "idle"]);
  assert.deepEqual(core.irdStatus(ok, true, 0, true)[0], "INJOIGNABLE");  // l'état connu le plus précis l'emporte
});

test("clé de porteuse plus fine (deux porteuses très proches) : fréquence relue", () => {
  assert.deepEqual(core.carrierKeyRef("125830.9-V"), { rf_khz: 12583090, polarization: "vertical" });
  assert.deepEqual(core.carrierKeyRef("125830.95-?"), { rf_khz: 12583095, polarization: null });
  assert.deepEqual(core.parseRoute("#/porteuse/125830.9-V").key, "125830.9-V");
});

test("BER à la française (virgule) comme les autres nombres", () => {
  assert.equal(core.fmtBer(1.2e-5), "1,2e-5");
  assert.equal(core.fmtBer(0), "< 1e-8");
});

test("clé à polarisation inconnue et deux porteuses V et H de même fréquence : pas de choix au hasard", () => {
  const v = { key: "125830-V", rf_khz: 12_583_020, polarization: "vertical", symbol_rate: 7_200_000 };
  const h = { key: "125830-H", rf_khz: 12_583_030, polarization: "horizontal", symbol_rate: 7_200_000 };
  assert.equal(core.matchCarrier([v, h], { key: "125830-?" }), null);
  assert.equal(core.matchCarrier([v], { key: "125830-?" }), v);  // une seule candidate : elle
  const u = { key: "125830-?", rf_khz: 12_583_000, polarization: null, symbol_rate: 7_200_000 };
  assert.equal(core.matchCarrier([v, h, u], { key: "125830-?" }), u);  // clé exacte d'abord
});

test("son d'une alerte : critique (alarme répétée), avertissement (un bip), selon les réglages", () => {
  const prefs = { critical: true, warning: true };
  const crit = { id: 1, severity: "critical", ts_end: null };
  const warn = { id: 2, severity: "warning", ts_end: null };
  assert.equal(core.alertSound(crit, prefs), "alarm");
  assert.equal(core.alertSound(warn, prefs), "beep");
  assert.equal(core.alertSound({ id: 3, severity: "info", ts_end: null }, prefs), null);
  assert.equal(core.alertSound({ ...crit, ts_end: 5 }, prefs), null);  // déjà fermée : rien
  assert.equal(core.alertSound(warn, { ...prefs, warning: false }), null);
  assert.equal(core.alertSound(crit, { ...prefs, critical: false }), null);
});
