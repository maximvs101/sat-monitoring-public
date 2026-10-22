"use strict";

// Analyseur FSH4 : état et réglages lus (jamais modifiés), Pause / Reprendre, plancher de bruit, tous les marqueurs de
// la période avec leur porteuse (pour retrouver le niveau RF d'une porteuse disparue), événements du FSH4.

const FshPage = (() => {
  let route = null;
  let dom = null;
  let unsubscribe = null;
  let data = null;
  let charts = { noise: null, all: null };
  let sigs = {};
  let seq = 0;

  const live = () => isLive(route.period);

  function go(changes) {
    location.hash = routeHref({ page: "fsh", period: route.period, ...changes });
  }

  function range() {
    return live() ? Store.liveRange() : [data.from, data.to];
  }

  function renderHead() {
    const s = Store.state;
    const info = s.fshInfo || {};
    const [label, tone] = DEVICE_STATE[s.states.fsh] || [s.states.fsh || "en attente", ""];
    const reason = s.states.fsh === "down" ? s.details.fsh : "";
    const meta = [info.host ? `${info.host}:${info.port}` : "", info.polarization ? `entrée ${POL_NAME[info.polarization] || info.polarization}` : "",
      info.span_hz ? `span ${num(info.span_hz / 1e6, 1)} MHz` : "", info.rbw_hz ? `RBW ${num(info.rbw_hz / 1e3, 0)} kHz` : "",
      s.seen.fsh ? `dernière mesure ${fmtDateTime(s.seen.fsh)}` : ""]
      .filter(Boolean).join(" · ");
    const sig = JSON.stringify([label, tone, reason, meta, s.states.fsh]);
    if (sig === sigs.head) return;
    sigs.head = sig;
    dom.state.className = `badge ${tone}`;
    dom.state.textContent = label;
    dom.meta.textContent = meta;
    dom.toggle.textContent = s.states.fsh === "paused" ? "Reprendre" : "Pause";
    dom.down.hidden = !reason || !live();
    dom.down.textContent = `Aucune mesure du FSH4 : ${reason}. Les mesures reprendront dès qu'il répondra à ses requêtes.`;
  }

  // Porteuse à laquelle chaque marqueur est rattaché sur la période ; « plancher de bruit » sinon.
  function owners() {
    const map = {};
    for (const c of data.carriers) for (const n of c.markers || []) map[n] = c.name;
    for (const n of data.noise || []) map[n] ??= "plancher de bruit";
    return map;
  }

  function stats(levels) {
    const v = (levels || []).filter((x) => x != null);
    if (!v.length) return null;
    return { min: Math.min(...v), max: Math.max(...v), avg: v.reduce((a, b) => a + b, 0) / v.length };
  }

  function renderCharts() {
    const fsh = data.fsh;
    const own = owners();
    const noise = (data.noise || []).filter((n) => fsh.levels[n]);
    const all = fsh.nums.filter((n) => fsh.levels[n]);
    const label = (n) => `${Charts.markerLabel(n, fsh.freqs)} · ${own[n] || "sans porteuse"}`;
    const sig = JSON.stringify([noise, all, all.map(label)]);
    if (sig !== sigs.charts) {
      sigs.charts = sig;
      for (const k of Object.keys(charts)) if (charts[k]) charts[k].destroy();
      charts = { noise: null, all: null };
      dom.noiseZone.replaceChildren();
      dom.allZone.replaceChildren();
      const onSelect = (from, to) => go({ period: `${from}-${to}` });
      if (noise.length) charts.noise = Charts.markerChart(dom.noiseZone, noise, fsh.freqs, { onSelect, height: 160 });
      else dom.noiseZone.append(el("p", { className: "muted", textContent: Store.state.states.fsh === "down" && live()
        ? "Pas de plancher de bruit : aucune mesure du FSH4."
        : "Pas de plancher de bruit : aucun marqueur placé hors porteuse sur la période." }));
      if (all.length) charts.all = Charts.markerChart(dom.allZone, all, fsh.freqs, { onSelect, height: 200, label });
      else dom.allZone.append(el("p", { className: "muted", textContent: "Aucun marqueur mesuré sur la période." }));
    }
    const [from, to] = range();
    for (const c of Object.values(charts)) if (c) { c.setData(fsh); c.setRange(from, to); }
    // Tableau des marqueurs : fréquence, porteuse, niveaux sur la période.
    const rows = all.map((n) => {
      const st = stats(fsh.levels[n]);
      return [`M${n}`, fsh.freqs[n] == null ? "—" : `${fmtMhz(fsh.freqs[n] / 1000)} MHz`, own[n] || "sans porteuse",
        st ? fmt(st.min, 1, "dBm") : "—", st ? fmt(st.avg, 1, "dBm") : "—", st ? fmt(st.max, 1, "dBm") : "—"];
    });
    const tsig = JSON.stringify(rows);
    if (tsig !== sigs.table) {
      sigs.table = tsig;
      dom.table.replaceChildren(el("thead", {}, el("tr", {}, ...["Marqueur", "Fréquence", "Porteuse", "Minimum", "Moyenne", "Maximum"]
        .map((h) => el("th", { textContent: h })))),
      el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((v, i) => el("td", { textContent: v, className: i >= 3 ? "num" : "" }))))));
      dom.table.hidden = !rows.length;
    }
  }

  function fshEvents() {
    const all = live() ? Store.liveEvents() : [...data.events].sort((a, b) => b.ts_start - a.ts_start);
    return all.filter((e) => e.source === "fsh" || e.kind === "marker_drop");
  }

  function renderEvents() {
    const events = fshEvents();
    const sig = JSON.stringify(events.map((e) => [e.id, e.ts_end]));
    if (sig === sigs.events) return;
    sigs.events = sig;
    const pad = Store.state.cfg.event_padding_s * 1000;
    dom.events.replaceChildren(events.length
      ? eventList(events, { onClick: (ev) => go({ period: `${ev.ts_start - pad}-${(ev.ts_end ?? Date.now()) + pad}` }) })
      : emptyState("Aucun événement du FSH4 sur la période."));
  }

  function render() {
    if (!dom || !data) return;
    renderHead();
    dom.ifBanner.hidden = !data.ifWarning;
    renderCharts();
    renderEvents();
  }

  function onStore() {
    if (!dom) return;
    if (live()) {
      data = Store.state;
      dom.status.textContent = liveStatus();
      render();
    } else {
      renderHead();
    }
  }

  async function loadPast() {
    const mine = ++seq;
    const [from, to] = periodRange(route.period, Date.now(), Store.state.cfg.live_window_min);
    dom.status.textContent = "chargement…";
    try {
      const past = await Store.loadRange(from, to, Math.max(500, Math.round((dom.allZone.clientWidth || 1000) * 1.5)));
      if (mine !== seq || !dom) return;
      data = past;
      dom.status.textContent = isFixedPeriod(route.period) ? "" : periodLabel(route.period, 0);
      render();
    } catch (e) {
      if (mine !== seq || !dom) return;
      dom.status.textContent = "";
      dom.error.hidden = false;
      dom.error.textContent = `Chargement impossible : ${e.message}`;
    }
  }

  function onResize() {
    Charts.fitWidth(Object.values(charts).filter(Boolean));
  }

  function mount(view, r) {
    route = r;
    dom = {
      state: el("span", { className: "badge" }),
      meta: el("span", { className: "meta" }),
      status: el("span", { className: "meta" }),
      toggle: button("Pause", () => toggleFshPause(), { small: true }),
      down: el("p", { className: "banner bad", hidden: true }),
      ifBanner: el("p", { className: "banner", hidden: true,
        textContent: "Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire." }),
      error: el("p", { className: "error", hidden: true }),
      table: el("table", { className: "table", hidden: true }),
      events: el("div"),
    };
    const [noiseCard, noiseZone] = Charts.chartCard("Plancher de bruit", "marqueurs hors porteuse · dBm");
    const [allCard, allZone] = Charts.chartCard("Tous les marqueurs", "avec leur porteuse · dBm");
    dom.noiseZone = noiseZone;
    dom.allZone = allZone;
    allCard.append(dom.table);
    const period = PeriodSelector.build(route.period || "live", (p) => go({ period: p === "live" ? undefined : p }));
    const head = el("div", { className: "page-head" }, el("h1", { textContent: "Analyseur FSH4" }), dom.state, dom.meta,
      el("div", { className: "actions" }, dom.status, dom.toggle, period));
    view.append(el("div", { className: "page" }, head, dom.down, dom.ifBanner, dom.error,
      el("div", { className: "carrier-main" }, el("div", { className: "chart-stack" }, noiseCard, allCard),
        el("div", { className: "carrier-side" }, cardBox("Événements du FSH4", dom.events)))));
    window.addEventListener("resize", onResize);
    renderHead();
    unsubscribe = Store.subscribe(onStore);
    if (live()) onStore();
    else loadPast();
  }

  function unmount() {
    seq++;
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    window.removeEventListener("resize", onResize);
    for (const c of Object.values(charts)) if (c) c.destroy();
    charts = { noise: null, all: null };
    sigs = {};
    dom = data = route = null;
  }

  return { mount, unmount };
})();
