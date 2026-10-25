"use strict";

// Éléments d'interface communs aux pages : en-têtes, listes d'événements, états vides, badges.

const KINDS = {
  lock_lost: "Perte de lock",
  ts_lock_lost: "Perte de lock TS",
  ber_high: "BER élevé",
  margin_drop: "Chute de marge",
  margin_low: "Marge basse",
  marker_drop: "Chute du niveau marqueur",
  device_down: "Appareil injoignable",
  fsh_settings: "Réglage FSH4 modifié",
  devices_changed: "Appareils changés",
  noise_rise: "Remontée du bruit",
  ird_alone: "IRD seul en défaut",
  video_lost: "Image perdue",
  audio_silent: "Son absent",
  not_descrambled: "Programme non déchiffré",
  ts_stopped: "Flux d'entrée arrêté",
  hw_fault: "Défaut matériel",
  ird_log: "Journal IRD",
};
const SEVERITY_TONE = { critical: "bad", warning: "warn", info: "ok" };
const DEVICE_STATE = { up: ["connecté", "ok"], down: ["injoignable", "bad"], paused: ["en pause", ""], unknown: ["en attente", ""],
  absent: ["aucun", ""] };  // absent : pas de FSH4 sur ce site (adresse vide)
const POL_NAME = { vertical: "verticale", horizontal: "horizontale" };
// Classe de couleur d'un code de la bande « État » (0 à 3), ou « down » / « idle ».
const TONE_OF_CODE = { 0: "ok", 1: "warn", 2: "violet", 3: "bad", down: "bad", idle: "" };

function pageHead(title, { meta = "", crumb = null, actions = [] } = {}) {
  const head = el("div", { className: "page-head" }, el("h1", { textContent: title }));
  if (meta) head.append(el("span", { className: "meta", textContent: meta }));
  if (actions.length) head.append(el("div", { className: "actions" }, ...actions));
  return crumb ? [el("div", { className: "crumb" }, ...crumb), head] : [head];
}

// État du direct pour l'en-tête des pages : l'heure n'avance que si le flux en direct est ouvert.
function liveStatus() {
  const s = Store.state;
  if (s.connected) return `en direct · ${fmtDateTime(Date.now())}`;
  const last = Math.max(0, ...Object.values(s.seen).filter(Boolean));
  return `connexion perdue${last ? ` · dernière mesure ${fmtDateTime(last)}` : ""}`;
}

// Pause / reprise de la lecture du FSH4 (rend la main sur l'appareil) ; erreur affichée par `onError`.
async function toggleFshPause(onError = (m) => alert(m)) {
  const action = Store.state.states.fsh === "paused" ? "resume" : "pause";
  try {
    await Store.post(`/api/fsh/${action}`);
  } catch (e) {
    onError(`${action === "pause" ? "Pause" : "Reprise"} du FSH4 impossible : ${e.message}`);
  }
}

function button(label, onClick, { primary = false, small = false } = {}) {
  const b = el("button", { type: "button", className: `btn${primary ? " primary" : ""}${small ? " small" : ""}`, textContent: label });
  b.addEventListener("click", onClick);
  return b;
}

function emptyState(text) {
  return el("p", { className: "empty", textContent: text });
}

function cardBox(title, ...children) {
  const box = el("section", { className: "card" });
  if (title) box.append(typeof title === "string" ? el("div", { className: "card-title", textContent: title }) : title);
  box.append(...children);
  return box;
}

// Source d'un événement en clair : nom de l'IRD, de la porteuse, ou « FSH4 ».
function eventSource(ev, names = Store.state.names) {
  const src = ev.source || "";
  if (src.startsWith("ird:")) return names[src.slice(4)] || src.slice(4);
  if (src.startsWith("porteuse:")) return Store.state.carriers.find((c) => c.key === src.slice(9))?.name || "porteuse";
  return "FSH4";
}

// Où mène un événement : la porteuse où se trouvait l'IRD au moment de l'événement (porteuses recalculées autour de
// lui, car l'IRD a pu être re-réglé depuis), la porteuse concernée, ou l'analyseur FSH4. { target } ou { message }.
async function eventDestination(ev) {
  const pad = Store.state.cfg.event_padding_s * 1000;
  let carriers = Store.state.carriers;
  if (ev.source?.startsWith("ird:")) {
    try {
      carriers = (await Store.loadRange(ev.ts_start - pad, (ev.ts_end ?? Date.now()) + pad, 50)).carriers;
    } catch (e) {
      return { message: `Chargement impossible : ${e.message}` };
    }
  }
  const target = eventTarget(ev, carriers, Date.now(), pad);
  return target ? { target } : { message: `${eventSource(ev)} n'appartenait à aucune porteuse à ce moment-là : pas de page à ouvrir.` };
}

// Liste d'événements : gravité en couleur, type, source, début → fin ou « en cours », détail. `onClick(ev)` facultatif ;
// `isLink(ev)` dit si un événement mène quelque part (sinon il n'a pas l'air cliquable).
function eventList(events, { onClick = null, isLink = () => true, names } = {}) {
  const list = el("ul", { className: "events" });
  for (const ev of events) {
    const when = el("div", { className: "when num",
      textContent: fmtDateTime(ev.ts_start) + (ev.ts_end == null ? "" : ` → ${fmtTime(ev.ts_end)}`) });
    if (ev.ts_end == null) when.append(el("span", { className: "open", textContent: " — en cours" }));
    const link = !!onClick && isLink(ev);
    const item = el("li", { className: `event${link ? " link" : ""}` },
      el("span", { className: `dot ${SEVERITY_TONE[ev.severity] || ""}` }),
      el("div", {}, el("span", { className: "what", textContent: KINDS[ev.kind] || ev.kind }),
        el("span", { className: "src", textContent: ` · ${eventSource(ev, names)}` }), when,
        el("div", { className: "detail", textContent: ev.detail })));
    if (link) {
      // Au clavier aussi : Tab jusqu'à l'événement, puis Entrée ou Espace.
      item.tabIndex = 0;
      item.setAttribute("role", "link");
      item.addEventListener("click", () => onClick(ev));
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(ev);
        }
      });
    }
    list.append(item);
  }
  return list;
}
