"use strict";

// Constantes et utilitaires partagés par les scripts de la page (chargé avant eux).

// Une couleur par numéro de marqueur FSH4 (1 à 6), une par IRD dans une porteuse.
const MARKER_COLORS = ["#2463eb", "#1f9d55", "#c98a12", "#8e44ad", "#d64545", "#0f8b8d"];
const IRD_COLORS = ["#1f9d55", "#c98a12", "#8e44ad", "#0f8b8d", "#d64545", "#2463eb", "#7a5c3e", "#b5367a"];
const POL_LETTER = { vertical: "V", horizontal: "H" };
// Codes de la bande « État » (band_state côté serveur) : couleur et libellé.
const STATE_COLORS = ["#1f9d55", "#c98a12", "#8e44ad", "#d64545"];
const STATE_LABELS = ["normal", "BER ou son", "image perdue / non déchiffré / flux arrêté", "lock perdu"];
const NO_DATA_COLOR = "#e3e6ec";

const $ = (id) => document.getElementById(id);

// Nombres à la française (num, fmt, fmtBer) : dans core.js, chargé avant.
const fmtTime = (ms) => new Date(ms).toLocaleTimeString("fr-FR");
const fmtDateTime = (ms) => new Date(ms).toLocaleString("fr-FR");
// Même écriture que les noms par défaut côté serveur (« 12 583,0 », espace ordinaire).
const fmtMhz = (khz) => (khz / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  .replace(/[  ]/g, " ");

// Élément HTML : propriétés (className, textContent, title…) et enfants.
function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  e.append(...children);
  return e;
}

// « hd_1920x1080i_50 » → « 1080i50 », ou « HD 1080i50 » avec la définition ; format inconnu : tel quel.
function imageFormat(f, withDefinition = false) {
  const m = /^(?:(\w+?)_)?(\d+)x(\d+)([ip])_?(\d+)/.exec(f || "");
  if (!m) return f || "";
  const def = withDefinition ? { hd: "HD ", sd: "SD ", uhd: "UHD " }[(m[1] || "").split("_")[0]] || "" : "";
  return `${def}${m[3]}${m[4]}${m[5]}`;
}

// Seuil de « Chute de marge » d'un IRD : médiane de sa dernière minute (hors 3 dernières s) moins la chute
// tolérée, comme la référence du détecteur (approximation : celle-ci reste figée pendant une alerte).
function dropThreshold(d, cfg) {
  if (!d || !d.ts.length || !cfg?.margin_drop_db) return null;
  const end = d.ts[d.ts.length - 1];
  const from = end - cfg.median_window_s * 1000, to = end - cfg.drop_confirm_s * 1000;
  const vals = d.margin_db.filter((v, i) => v != null && d.ts[i] >= from && d.ts[i] <= to).sort((a, b) => a - b);
  return vals.length >= 10 ? vals[Math.floor(vals.length / 2)] - cfg.margin_drop_db : null;
}
