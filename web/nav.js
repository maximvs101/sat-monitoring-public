"use strict";

// Menu latéral : vue d'ensemble, porteuses actuelles, journal, analyseur FSH4, réglages ; nombre d'alertes en cours.

const Nav = (() => {
  let root = null, route = { page: "overview" }, sig = null;

  // Alertes en cours d'une porteuse : celles de ses IRD et de ses marqueurs.
  function carrierAlerts(c) {
    return Store.openEvents((e) => e.source === `porteuse:${c.key}` || c.irds.some((s) => e.source === `ird:${s}`)).length;
  }

  function item(label, href, { active = false, sub = false, count = 0, tone = "bad" } = {}) {
    const a = el("a", { className: `nav-item${sub ? " sub" : ""}${active ? " active" : ""}`, href }, el("span", { textContent: label }));
    if (count) a.append(el("span", { className: `badge ${tone}`, textContent: String(count) }));
    return a;
  }

  function render() {
    const s = Store.state;
    const carriers = s.carriers.map((c) => [c.key, c.name, carrierAlerts(c)]);
    const total = Store.openEvents().length;
    const next = JSON.stringify([route, carriers, total, s.connected, s.states.fsh]);
    if (next === sig) return;
    sig = next;
    const is = (page, extra = {}) => route.page === page && Object.entries(extra).every(([k, v]) => route[k] === v);
    // Porteuse ouverte par une clé devenue autre (polarisation connue entre-temps…) : celle que la page affiche.
    const shown = route.page === "carrier" ? matchCarrier(s.carriers, { key: route.key }) : null;
    root.replaceChildren(
      el("div", { className: "nav-brand", textContent: "Monitoring satellite" }),
      el("div", { className: `nav-live${s.connected ? "" : " off"}`, textContent: s.connected ? "● en direct" : "● connexion perdue" }),
      item("Vue d'ensemble", routeHref({ page: "overview" }), { active: is("overview"), count: total }),
      el("div", { className: "nav-group", textContent: "Porteuses" }),
      ...(carriers.length ? carriers.map(([key, name, count]) =>
        item(name, routeHref({ page: "carrier", key }), { sub: true, active: shown?.key === key, count }))
        : [el("div", { className: "nav-item sub muted", textContent: "aucune" })]),
      el("div", { className: "nav-sep" }),
      item("Journal des événements", routeHref({ page: "journal" }), { active: is("journal") }),
      item("Analyseur FSH4", routeHref({ page: "fsh" }), { active: is("fsh") }),
      el("div", { className: "nav-group", textContent: "Réglages" }),
      item("Appareils", routeHref({ page: "settings", tab: "appareils" }), { active: is("settings", { tab: "appareils" }) }),
      item("Porteuses", routeHref({ page: "settings", tab: "porteuses" }), { active: is("settings", { tab: "porteuses" }) }),
      item("Seuils d'alerte", routeHref({ page: "settings", tab: "seuils" }), { active: is("settings", { tab: "seuils" }) }),
    );
  }

  function init(el_) {
    root = el_;
    Store.subscribe((kind) => { if (kind !== "tick") render(); });
  }

  function setRoute(r) {
    route = r;
    render();
  }

  return { init, setRoute };
})();
