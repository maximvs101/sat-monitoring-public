"use strict";

// Logique de la page sans DOM : adresses des pages, périodes, porteuses, résumés, formats.
// Chargé en premier dans le navigateur (fonctions globales) et testé sous Node (web/tests/core.test.js).

const PAGES = { porteuse: "carrier", journal: "journal", fsh4: "fsh", reglages: "settings" };
const SETTINGS_TABS = ["appareils", "porteuses", "seuils"];
const QUERY_KEYS = { p: "period", ird: "ird", src: "src", open: "open" };
const FIXED_PERIODS = { "1h": 3_600_000, "24h": 86_400_000, "7j": 7 * 86_400_000, "30j": 30 * 86_400_000 };
const FIXED_LABELS = { "1h": "1 h", "24h": "24 h", "7j": "7 j", "30j": "30 j" };
const CUSTOM_PERIOD = /^(\d+)-(\d+)$/;
const POLARIZATIONS = { V: "vertical", H: "horizontal" };
const DEFAULT_TOLERANCE_KHZ = 1_000;  // comme monitoring/carriers.py, sans débit symbole connu

// --- Formats à la française ---

const num = (v, digits) => v.toFixed(digits).replace(".", ",");
const fmt = (v, digits, unit) => (v == null ? "—" : `${num(v, digits)} ${unit}`);
const fmtBer = (v) => (v == null ? "—" : v <= 1e-8 ? "< 1e-8" : v.toExponential(1));

// --- Adresses des pages : #/, #/porteuse/<clé>, #/journal, #/fsh4, #/reglages/<onglet> ; paramètres après « ? » ---

function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "");
  const q = raw.indexOf("?");
  const path = q < 0 ? raw : raw.slice(0, q);
  const parts = path.split("/").filter(Boolean);
  let route;
  if (!parts.length) route = { page: "overview" };
  else if (parts[0] === "porteuse" && parts.length === 2) route = { page: "carrier", key: decodeURIComponent(parts[1]) };
  else if ((parts[0] === "journal" || parts[0] === "fsh4") && parts.length === 1) route = { page: PAGES[parts[0]] };
  else if (parts[0] === "reglages" && parts.length <= 2) {
    const tab = parts[1] || "appareils";
    route = SETTINGS_TABS.includes(tab) ? { page: "settings", tab } : { page: "notfound" };
  } else route = { page: "notfound" };
  if (q >= 0 && route.page !== "notfound") {
    for (const [name, value] of new URLSearchParams(raw.slice(q + 1))) {
      if (QUERY_KEYS[name] && value) route[QUERY_KEYS[name]] = value;
    }
  }
  return route;
}

function routeHref(route) {
  const path = { overview: "/", carrier: `/porteuse/${encodeURIComponent(route.key || "")}`, journal: "/journal",
    fsh: "/fsh4", settings: `/reglages/${route.tab || "appareils"}` }[route.page] || "/";
  const params = Object.entries(QUERY_KEYS).filter(([, field]) => route[field]).map(([name, field]) =>
    `${name}=${encodeURIComponent(route[field])}`);
  return `#${path}${params.length ? `?${params.join("&")}` : ""}`;
}

// --- Périodes : « live » (fenêtre du direct), 1h, 24h, 7j, 30j, ou « <début>-<fin> » en ms ---

function isLive(period) {
  return !period || period === "live" || !(period in FIXED_PERIODS || CUSTOM_PERIOD.test(period));
}

function periodRange(period, now, liveMin) {
  if (period in FIXED_PERIODS) return [now - FIXED_PERIODS[period], now];
  const m = CUSTOM_PERIOD.exec(period || "");
  if (m && Number(m[1]) < Number(m[2])) return [Number(m[1]), Number(m[2])];
  return [now - liveMin * 60_000, now];
}

function periodLabel(period, liveMin) {
  if (period in FIXED_LABELS) return FIXED_LABELS[period];
  const m = CUSTOM_PERIOD.exec(period || "");
  if (m && Number(m[1]) < Number(m[2])) {
    const d = (ms) => new Date(ms).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    return `du ${d(Number(m[1]))} au ${d(Number(m[2]))}`;
  }
  return `Direct ${liveMin} min`;
}

// --- Porteuses ---

// Fréquence et polarisation d'une clé « <rf_khz // 100>-V|H|? ».
function carrierKeyRef(key) {
  const m = /^(\d+)-([VH?])$/.exec(key || "");
  return m ? { rf_khz: Number(m[1]) * 100, polarization: POLARIZATIONS[m[2]] || null } : null;
}

function _close(a, b) {
  const rates = [a.symbol_rate, b.symbol_rate].filter(Boolean);
  const tolerance = rates.length ? Math.min(...rates) / 2 / 1000 : DEFAULT_TOLERANCE_KHZ;
  return Math.abs(a.rf_khz - b.rf_khz) < tolerance;
}

// Porteuse désignée par `ref` ({ key } ou { rf_khz, polarization, symbol_rate }) : la clé exacte, sinon la plus
// proche en fréquence de même polarisation (ou inconnue d'un côté), à la tolérance de regroupement près.
function matchCarrier(carriers, ref) {
  if (ref.key) {
    const exact = carriers.find((c) => c.key === ref.key);
    if (exact) return exact;
  }
  const target = ref.rf_khz != null ? ref : carrierKeyRef(ref.key);
  if (!target) return null;
  const candidates = carriers.filter((c) => (!c.polarization || !target.polarization || c.polarization === target.polarization)
    && _close(c, target));
  candidates.sort((a, b) => Math.abs(a.rf_khz - target.rf_khz) - Math.abs(b.rf_khz - target.rf_khz));
  return candidates[0] || null;
}

// --- Résumé d'une période pour un IRD (hors Direct) ---

// Marge minimale, durée passée dans chaque code de la bande « État », nombre d'alertes. Chaque mesure compte
// jusqu'à la suivante, sauf au-delà d'un trou (coupure) : au plus deux fois l'intervalle habituel, 3 s au moins.
function periodSummary(series, events) {
  const state_ms = { 0: 0, 1: 0, 2: 0, 3: 0 };
  const margins = series.margin_db.filter((v) => v != null);
  const steps = series.ts.slice(1).map((t, i) => t - series.ts[i]).sort((a, b) => a - b);
  const maxGap = Math.max(3000, 2 * (steps[Math.floor(steps.length / 2)] || 0));
  for (let i = 0; i + 1 < series.ts.length; i++) {
    const code = series.state[i];
    const dt = series.ts[i + 1] - series.ts[i];
    if (code != null && code in state_ms && dt <= maxGap) state_ms[code] += dt;
  }
  return { margin_min: margins.length ? Math.min(...margins) : null, state_ms, alerts: events.length };
}

// --- État d'un IRD en clair : [titre du badge, cause, code de couleur (0 à 3, « down » ou « idle »)] ---

function irdStatus(m, down, minimum) {
  if (down) return ["INJOIGNABLE", "l'IRD ne répond plus", "down"];
  if (!m) return ["EN ATTENTE", "pas encore de mesure", "idle"];
  const code = m.state ?? 0;
  if (!m.locked) return ["LOCK PERDU", "plus de signal démodulé", code];
  if (!m.ts_locked) return ["LOCK TS PERDU", "flux transport non verrouillé", code];
  if (m.video_active === false) return ["PAS D'IMAGE", "le décodeur ne reçoit plus de vidéo", code];
  if (m.scrambled && m.descrambled === false) return ["NON DÉCHIFFRÉ", "programme chiffré non déchiffré", code];
  if (m.bitrate_kbps === 0) return ["FLUX ARRÊTÉ", "débit d'entrée à zéro", code];
  if (m.audio_configured && m.audio_active === 0 && m.video_active) return ["SON ABSENT", "aucune voie audio active", code];
  if (m.ber) return ["ERREURS", `BER ${fmtBer(m.ber)} : réception à la limite`, code];
  if (minimum > 0 && m.margin_db != null && m.margin_db < minimum) {
    return ["MARGE BASSE", `sous le minimum de ${fmt(minimum, 1, "dB")}`, Math.max(code, 1)];
  }
  return ["OK", "", code];
}

// --- Où mène le clic sur un événement (journal, listes) ---

// `carriersAt` : porteuses de la période autour de l'événement (avec, par IRD, ses périodes d'appartenance).
// IRD : la porteuse où il était à ce moment-là ; FSH4 : la page de l'analyseur ; sinon rien.
function eventTarget(ev, carriersAt, now, padMs) {
  const period = `${ev.ts_start - padMs}-${(ev.ts_end ?? now) + padMs}`;
  const src = ev.source || "";
  if (src === "fsh") return { page: "fsh", period };
  if (src.startsWith("porteuse:")) return { page: "carrier", key: src.slice(9), period };
  if (src.startsWith("ird:")) {
    const serial = src.slice(4);
    const carrier = carriersAt.find((c) => c.irds.includes(serial)
      && (!c.periods?.[serial] || c.periods[serial].some(([from, to]) => ev.ts_start >= from && ev.ts_start <= to)));
    return carrier ? { page: "carrier", key: carrier.key, period } : null;
  }
  return null;
}

if (typeof module !== "undefined") {
  module.exports = { num, fmt, fmtBer, parseRoute, routeHref, isLive, periodRange, periodLabel, carrierKeyRef,
    matchCarrier, periodSummary, irdStatus, eventTarget };
}
