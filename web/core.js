"use strict";

// Logique de la page sans DOM : adresses des pages, périodes, porteuses, résumés, formats.
// Chargé en premier dans le navigateur (fonctions globales) et testé sous Node (web/tests/core.test.js).

const PAGES = { porteuse: "carrier", journal: "journal", fsh4: "fsh", reglages: "settings" };
const SETTINGS_TABS = ["appareils", "porteuses", "seuils", "notifications"];
const QUERY_KEYS = { p: "period", ird: "ird", src: "src", open: "open" };
const FIXED_PERIODS = { "1h": 3_600_000, "24h": 86_400_000, "7j": 7 * 86_400_000, "30j": 30 * 86_400_000 };
const FIXED_LABELS = { "1h": "1 h", "24h": "24 h", "7j": "7 j", "30j": "30 j" };
const CUSTOM_PERIOD = /^(\d+)-(\d+)$/;
const POLARIZATIONS = { V: "vertical", H: "horizontal" };
const DEFAULT_TOLERANCE_KHZ = 1_000;  // comme monitoring/carriers.py, sans débit symbole connu

// --- Formats à la française ---

const num = (v, digits) => v.toFixed(digits).replace(".", ",");
const fmt = (v, digits, unit) => (v == null ? "—" : `${num(v, digits)} ${unit}`);
const fmtBer = (v) => (v == null ? "—" : v <= 1e-8 ? "< 1e-8" : v.toExponential(1).replace(".", ","));

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

// Période prédéfinie (1h, 24h, 7j, 30j) ; propres clés seulement (« constructor » n'en est pas une).
function isFixedPeriod(period) {
  return typeof period === "string" && Object.hasOwn(FIXED_PERIODS, period);
}

function isLive(period) {
  return !period || period === "live" || !(isFixedPeriod(period) || CUSTOM_PERIOD.test(period));
}

function periodRange(period, now, liveMin) {
  if (isFixedPeriod(period)) return [now - FIXED_PERIODS[period], now];
  const m = CUSTOM_PERIOD.exec(period || "");
  if (m && Number(m[1]) < Number(m[2])) return [Number(m[1]), Number(m[2])];
  return [now - liveMin * 60_000, now];
}

function periodLabel(period, liveMin) {
  if (isFixedPeriod(period)) return FIXED_LABELS[period];
  const m = CUSTOM_PERIOD.exec(period || "");
  if (m && Number(m[1]) < Number(m[2])) {
    const d = (ms) => new Date(ms).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    return `du ${d(Number(m[1]))} au ${d(Number(m[2]))}`;
  }
  return `Direct ${liveMin} min`;
}

// --- Porteuses ---

// Fréquence et polarisation d'une clé « <rf_khz // 100>-V|H|? » (ou plus fine : « 125830.9-V »).
function carrierKeyRef(key) {
  const m = /^(\d+(?:\.\d+)?)-([VH?])(?:-\d+)?$/.exec(key || "");
  return m ? { rf_khz: Math.round(Number(m[1]) * 100), polarization: POLARIZATIONS[m[2]] || null } : null;
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
  // Polarisation inconnue et porteuses V et H à cette fréquence : impossible de savoir laquelle, aucune.
  if (!target.polarization && new Set(candidates.map((c) => c.polarization).filter(Boolean)).size > 1) {
    return candidates.find((c) => !c.polarization) || null;
  }
  return candidates[0] || null;
}

// --- Résumé d'une période pour un IRD (hors Direct) ---

// Marge minimale, durée passée dans chaque code de la bande « État », nombre d'alertes. Chaque mesure compte
// jusqu'à la suivante, sauf au-delà d'un trou (coupure) : au plus deux fois l'intervalle habituel, 3 s au moins.
// Série agrégée : le service donne le temps réel passé dans chaque code par intervalle (state_ms), à additionner.
function periodSummary(series, events) {
  const state_ms = { 0: 0, 1: 0, 2: 0, 3: 0 };
  const margins = series.margin_db.filter((v) => v != null);
  if (series.state_ms) {
    for (const bucket of series.state_ms) for (let k = 0; k < 4; k++) state_ms[k] += bucket[k];
    return { margin_min: margins.length ? Math.min(...margins) : null, state_ms, alerts: events.length };
  }
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

// `stale` : direct coupé ou plus de mesure depuis un moment ; la dernière mesure n'est plus l'état de l'IRD.
function irdStatus(m, down, minimum, stale = false) {
  if (down) return ["INJOIGNABLE", "l'IRD ne répond plus", "down"];
  if (!m) return ["EN ATTENTE", "pas encore de mesure", "idle"];
  if (stale) return ["SANS NOUVELLES", "aucune mesure reçue récemment", "idle"];
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

// --- Son des alertes dans la page ---

// « alarm » (critique, répétée jusqu'à acquittement), « beep » (avertissement, une fois) ou null. `prefs` : réglages
// du navigateur { critical, warning }.
function alertSound(ev, prefs) {
  if (ev.ts_end != null) return null;
  if (ev.severity === "critical") return prefs.critical ? "alarm" : null;
  if (ev.severity === "warning") return prefs.warning ? "beep" : null;
  return null;
}

const STOPPED_REASON = "— service arrêté";  // ajouté par le service aux alertes qu'il ferme en s'arrêtant

// Une alerte se reconnaît d'une ouverture à l'autre par son appareil et son type.
function alertKey(ev) {
  return `${ev.source}|${ev.kind}`;
}

// Terminée parce que le défaut a cessé (et non fermée par l'arrêt du service, le défaut pouvant durer encore).
function reallyEnded(ev) {
  return ev.ts_end != null && !(ev.detail || "").endsWith(STOPPED_REASON);
}

// Son d'une alerte qui vient de s'ouvrir : `ackedKeys` (alertes acquittées, par appareil et type, pas encore vues se
// terminer vraiment), `muted` ({ source: fin du silence en ms }), `now`. Pas d'alarme pour un appareil en silence
// (intervention), ni pour une alerte acquittée que le service, redémarré, rouvre (même panne).
function alarmFor(ev, { prefs, ackedKeys, muted, now }) {
  const sound = alertSound(ev, prefs);
  if (!sound) return null;
  if (muted[ev.source] > now) return null;
  if (ackedKeys.has(alertKey(ev))) return null;
  return sound;
}

// Répéter l'alarme maintenant ? `r` : { since (début de l'alarme), next (prochaine répétition) } ; arrêt après
// `repeat_max_min` minutes (0 = jamais), pas de répétition si `repeat_s` vaut 0.
// Plusieurs séries sur un même axe de temps (IRD d'une porteuse, marqueurs du FSH4) : [ts, { champ: [une colonne par
// source] }]. `undefined` là où une source n'a pas de point (la courbe passe par-dessus), `null` juste après un trou de
// mesure de la source (service arrêté, appareil injoignable, IRD re-réglé) : la courbe s'interrompt au lieu de tracer
// une ligne droite à travers une période sans mesure. Trou : plus de 3 fois le pas habituel, et au moins 10 s.
const GAP_MIN_MS = 10_000;

function gapThreshold(ts) {
  const steps = [];
  for (let i = 1; i < ts.length; i++) steps.push(ts[i] - ts[i - 1]);
  if (!steps.length) return GAP_MIN_MS;
  steps.sort((a, b) => a - b);
  return Math.max(GAP_MIN_MS, 3 * steps[Math.floor(steps.length / 2)]);
}

function alignSeries(sources, fields) {
  const breaks = sources.map((src) => {
    const limit = gapThreshold(src.ts);
    const out = [];
    for (let i = 1; i < src.ts.length; i++) if (src.ts[i] - src.ts[i - 1] > limit) out.push(src.ts[i - 1] + 1);
    return out;
  });
  const ts = [...new Set([...sources.flatMap((src) => src.ts), ...breaks.flat()])].sort((a, b) => a - b);
  const index = new Map(ts.map((t, i) => [t, i]));
  const cols = Object.fromEntries(fields.map((f) => [f, sources.map(() => new Array(ts.length).fill(undefined))]));
  sources.forEach((src, k) => {
    src.ts.forEach((t, i) => {
      for (const f of fields) cols[f][k][index.get(t)] = src.values[f]?.[i] ?? null;
    });
    for (const t of breaks[k]) for (const f of fields) cols[f][k][index.get(t)] = null;
  });
  return [ts, cols];
}

// Étiquettes de seuils sur un graphe (positions verticales en pixels, vers le bas) : écartées d'au moins `gap` quand
// leurs lignes sont proches, sans changer leur ordre, dans [min, max]. Renvoyées dans l'ordre d'entrée.
function spreadLabels(ys, gap, min, max) {
  const order = ys.map((y, i) => i).sort((a, b) => ys[a] - ys[b] || a - b);
  const out = ys.slice();
  order.forEach((i, k) => { out[i] = k ? Math.max(ys[i], out[order[k - 1]] + gap) : Math.max(ys[i], min); });
  for (let k = order.length - 1; k >= 0; k--) {
    const limit = k === order.length - 1 ? max : out[order[k + 1]] - gap;
    out[order[k]] = Math.min(out[order[k]], limit);
  }
  return out;
}

// Grille des tuiles d'IRD de la vue d'ensemble : nombre de colonnes et hauteur des tuiles qui remplissent au mieux
// la zone (width × height, en px) jusqu'à 9 IRD. Une disposition est notée par la taille de ses tuiles (hauteur
// plafonnée) rapportée à une tuile de référence 300 × 200 ; à égalité, le moins de rangées (pas de tuile seule sur
// une rangée quand elles tiennent côte à côte). Hauteur plafonnée : pas de tuile géante avec peu d'IRD (la largeur
// remplit la colonne). Au-delà de 9, la grille de 9 et la page défile.
// TILE_MIN_H : une tuile en défaut (ligne de cause comprise) tient en entier ; en dessous, la page défile.
const TILE_GAP = 12, TILE_REF = [300, 200], TILE_MAX_H = 420, TILE_MIN_H = 145, TILE_FIT_MAX = 9;

function tileGrid(n, width, height) {
  if (n <= 0) return { cols: 1, tileH: TILE_MAX_H };
  const count = Math.min(n, TILE_FIT_MAX);
  let best = null;
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols);
    const w = (width - TILE_GAP * (cols - 1)) / cols;
    const h = Math.min((height - TILE_GAP * (rows - 1)) / rows, TILE_MAX_H);
    const score = Math.min(w / TILE_REF[0], h / TILE_REF[1]);
    if (!best || score > best.score + 1e-9 || (Math.abs(score - best.score) <= 1e-9 && rows < best.rows)) {
      best = { cols, rows, h, score };
    }
  }
  return { cols: best.cols, tileH: Math.max(best.h, TILE_MIN_H) };
}

// Chiffre de marge d'une tuile : rouge au décrochage (0 dB) ou sous le minimum réglé, sur une mesure fraîche seulement.
function marginTone(margin, minimum, live) {
  if (!live || margin == null) return "";
  return margin <= 0 || (minimum > 0 && margin < minimum) ? "bad" : "";
}

// Aperçu de l'image décodée : son heure est celle de la dernière image nouvelle reçue par le service. Ancienne
// (IRD figé, injoignable, ou direct coupé), la carte la grise ; la page compose « préfixe + heure ».
const THUMB_STALE_MS = 10_000;

function thumbView(thumbTs, now, connected) {
  if (thumbTs == null) return { state: "none", prefix: "pas d'image" };
  if (connected && now - thumbTs <= THUMB_STALE_MS) return { state: "fresh", prefix: "image " };
  return { state: "old", prefix: "image de " };
}

function repeatDue(r, now, prefs) {
  if (!prefs.repeat_s || now < r.next) return false;
  return !prefs.repeat_max_min || now - r.since < prefs.repeat_max_min * 60_000;
}

if (typeof module !== "undefined") {
  module.exports = { num, fmt, fmtBer, parseRoute, routeHref, isFixedPeriod, isLive, periodRange, periodLabel, carrierKeyRef,
    matchCarrier, periodSummary, irdStatus, eventTarget, alertSound, alertKey, reallyEnded, alarmFor, repeatDue, thumbView, alignSeries, spreadLabels, tileGrid, marginTone };
}
