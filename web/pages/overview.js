"use strict";

// Vue d'ensemble : chiffres clés, une carte par IRD suivi (grille adaptée à leur nombre), événements en cours et
// récents, plancher de bruit du FSH4. Un clic sur une carte ouvre la page de sa porteuse.

const OverviewPage = (() => {
  const HINT = "Ouvrir la page de sa porteuse";
  const MAX_EVENTS = 15;
  let unsubscribe = null;
  let dom = null;
  const cards = new Map();  // numéro de série → carte
  const sigs = {};
  let noiseChart = null;

  function carrierOf(serial) {
    return Store.state.carriers.find((c) => c.irds.includes(serial)) || null;
  }

  function openIrd(serial) {
    const c = carrierOf(serial);
    location.hash = c ? routeHref({ page: "carrier", key: c.key, ird: serial }) : routeHref({ page: "settings", tab: "appareils" });
  }

  // IRD suivis, rangés par porteuse (ordre des fréquences) puis par nom.
  function orderedSerials() {
    const s = Store.state;
    const rank = (serial) => { const i = s.carriers.indexOf(carrierOf(serial)); return i < 0 ? Infinity : i; };
    return [...s.followed].sort((a, b) => rank(a) - rank(b)
      || (s.names[a] || a).localeCompare(s.names[b] || b, "fr", { numeric: true }));
  }

  function kpi(label, value, { tone = "", small = "", foot = null } = {}) {
    const v = el("div", { className: `value ${tone}`, textContent: value });
    if (small) v.append(" ", el("small", { textContent: small }));
    const box = el("div", { className: "kpi" }, el("div", { className: "label", textContent: label }), v);
    if (foot) box.append(foot);
    return box;
  }

  function renderKpis() {
    const s = Store.state;
    const up = s.followed.filter((x) => s.states[`ird:${x}`] === "up").length;
    const open = Store.openEvents();
    let low = null;
    for (const serial of s.followed) {
      const m = s.latest[serial];
      if (m && m.locked && s.states[`ird:${serial}`] !== "down" && m.margin_db != null && (!low || m.margin_db < low.v)) {
        low = { v: m.margin_db, serial };
      }
    }
    const [fshLabel, fshTone] = DEVICE_STATE[s.states.fsh] || [s.states.fsh || "en attente", ""];
    const sig = JSON.stringify([s.connected, up, s.followed.length, open.length, open.some((e) => e.severity === "critical"),
      low && [low.v, low.serial], s.states.fsh, s.names]);
    if (sig === sigs.kpis) return;
    sigs.kpis = sig;
    dom.kpis.replaceChildren(
      s.connected ? kpi("IRD en service", `${up} / ${s.followed.length}`, { tone: up < s.followed.length ? "bad" : "" })
        : kpi("IRD en service", "—", { small: "connexion perdue" }),
      kpi("Alertes en cours", String(open.length),
        { tone: open.some((e) => e.severity === "critical") ? "bad" : open.length ? "warn" : "" }),
      kpi("Marge la plus basse", low ? fmt(low.v, 1, "dB") : "—", { small: low ? s.names[low.serial] || low.serial : "" }),
      s.states.fsh === "absent" ? kpi("Analyseur FSH4", fshLabel, { small: "pas de FSH4 sur ce site" })
        : kpi("Analyseur FSH4", fshLabel, { tone: fshTone === "bad" ? "bad" : "",
          foot: el("div", { className: "foot" }, button(s.states.fsh === "paused" ? "Reprendre" : "Pause", () => toggleFshPause(), { small: true }),
            el("a", { href: routeHref({ page: "fsh" }), textContent: "Analyseur →" })) }),
    );
  }

  function renderCards() {
    const s = Store.state;
    const serials = orderedSerials();
    for (const [serial, h] of cards) {
      if (!serials.includes(serial)) { h.el.remove(); cards.delete(serial); }
    }
    dom.empty.hidden = serials.length > 0;
    const top = dom.grid.getBoundingClientRect().top + window.scrollY;
    const layout = Card.layout(serials.length, dom.grid.clientWidth, Math.max(220, window.innerHeight - top - 24));
    Card.applyLayout(dom.grid, layout);
    const range = Store.liveRange();
    serials.forEach((serial, i) => {
      let h = cards.get(serial);
      if (!h) { h = Card.build(serial, { onClick: openIrd, hint: HINT }); cards.set(serial, h); }
      if (dom.grid.children[i] !== h.el) dom.grid.insertBefore(h.el, dom.grid.children[i] || null);
      Card.update(h, {
        latest: s.latest[serial], down: s.states[`ird:${serial}`] === "down", deviceState: s.states[`ird:${serial}`],
        series: s.irds[serial], carrier: carrierOf(serial), openAlerts: Store.openEvents((e) => e.source === `ird:${serial}`).length,
        cfg: s.cfg, range, scale: layout.scale, compact: layout.compact,
      });
    });
  }

  function targetOf(ev) {
    return eventTarget(ev, Store.state.carriers, Date.now(), Store.state.cfg.event_padding_s * 1000);
  }

  // Événement d'un IRD : la porteuse où il était à ce moment-là (il a pu être re-réglé depuis), comme le journal.
  async function onEventClick(ev) {
    const page = dom;
    const { target, message } = await eventDestination(ev);
    if (dom !== page) return;
    if (target) location.hash = routeHref(target);
    else {
      dom.notice.hidden = false;
      dom.notice.textContent = message;
    }
  }

  function renderEvents() {
    const events = Store.liveEvents().slice(0, MAX_EVENTS);
    const sig = JSON.stringify(events.map((e) => [e.id, e.ts_end, e.detail]));
    if (sig === sigs.events) return;
    sigs.events = sig;
    dom.events.replaceChildren(events.length ? eventList(events, { onClick: onEventClick, isLink: (ev) => ev.source?.startsWith("ird:") || !!targetOf(ev) }) : emptyState("Aucun événement récent."));
  }

  // Plancher de bruit : marqueurs placés hors porteuse (recréé quand ils changent).
  function renderNoise() {
    const s = Store.state;
    const nums = s.noise.filter((n) => s.fsh.levels[n]);
    dom.noise.hidden = !nums.length;
    const sig = JSON.stringify(nums.map((n) => [n, s.fsh.freqs[n]]));
    if (sig !== sigs.noise) {
      sigs.noise = sig;
      if (noiseChart) noiseChart.destroy();
      noiseChart = nums.length ? Charts.markerChart(dom.noiseZone, nums, s.fsh.freqs, { height: 130 }) : null;
    }
    if (noiseChart) {
      noiseChart.setData(s.fsh);
      noiseChart.setRange(...Store.liveRange());
    }
  }

  function onResize() {
    render();
    if (noiseChart) Charts.fitWidth([noiseChart]);
  }

  function render() {
    const s = Store.state;
    dom.meta.textContent = liveStatus();
    dom.ifBanner.hidden = !s.ifWarning;
    renderKpis();
    renderCards();
    renderEvents();
    renderNoise();
  }

  function mount(view) {
    dom = {
      meta: el("span", { className: "meta" }),
      ifBanner: el("p", { className: "banner", textContent: "Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire." }),
      kpis: el("div", { className: "kpis" }),
      grid: el("div", { className: "ird-grid" }),
      empty: emptyState("Aucun IRD suivi : en ajouter un dans Réglages › Appareils."),
      events: el("div"),
      notice: el("p", { className: "muted note", hidden: true }),  // événement sans page à ouvrir
    };
    [dom.noise, dom.noiseZone] = Charts.chartCard("Plancher de bruit (FSH4)", "marqueurs hors porteuse · dBm");
    dom.noise.querySelector(".card-title .unit").append(" · ", el("a", { href: routeHref({ page: "fsh" }), textContent: "Analyseur →" }));
    dom.noise.hidden = true;
    const head = el("div", { className: "page-head" }, el("h1", { textContent: "Vue d'ensemble" }), dom.meta);
    const side = el("div", { className: "overview-side" },
      cardBox(el("div", { className: "card-title" }, el("span", { textContent: "Événements en cours et récents" }),
        el("a", { href: routeHref({ page: "journal" }), className: "unit", textContent: "Tout le journal →" })), dom.notice, dom.events));
    view.append(el("div", { className: "page" }, head, dom.ifBanner, dom.kpis,
      el("div", { className: "overview-main" }, el("div", {}, dom.grid, dom.empty, dom.noise), side)));
    render();
    unsubscribe = Store.subscribe(render);
    window.addEventListener("resize", onResize);
  }

  function unmount() {
    if (unsubscribe) unsubscribe();
    window.removeEventListener("resize", onResize);
    unsubscribe = null;
    cards.clear();
    if (noiseChart) noiseChart.destroy();
    noiseChart = null;
    for (const k of Object.keys(sigs)) delete sigs[k];
    dom = null;
  }

  return { mount, unmount };
})();
