"use strict";

// Page d'une porteuse : en-tête, période (dans l'adresse), courbes communes à ses IRD (marge, puissance, niveau RF,
// bande État) sur un même axe avec curseur synchronisé, une section par IRD (carte et détails en Direct, résumé de la
// période sinon), événements de la porteuse. Hors Direct, la porteuse est retrouvée parmi celles de la période par sa
// fréquence et sa polarisation : elle peut ne plus exister aujourd'hui.

const CarrierPage = (() => {
  const CODECS = { h264: "H.264", hevc: "HEVC", h265: "HEVC", mpeg2: "MPEG-2", mpa: "MPEG audio", ac3: "Dolby Digital",
    eac3: "Dolby Digital Plus", aac: "AAC", heaac: "HE-AAC", pcm: "PCM", dolbye: "Dolby E" };
  const PATTERNS = { blue: "mire bleue", black: "image noire", red: "mire rouge" };
  const codec = (c) => CODECS[c] || c || "?";

  let route = null;
  let dom = null;
  let unsubscribe = null;
  let charts = null;     // { sig, margin, power, band, rf }
  let carrier = null;    // porteuse affichée
  let data = null;       // Direct : Store.state ; hors Direct : période chargée
  let sections = new Map();  // numéro de série → { el, card, details }
  let sigs = {};
  let scrolled = false;
  let seq = 0;

  const live = () => isLive(route.period);

  function go(changes) {
    location.hash = routeHref({ page: "carrier", key: route.key, period: route.period, ...changes, ird: undefined });
  }

  // Panneau « Détails » d'un IRD : image, voies audio, configuration des sorties. Lignes [libellé, texte].
  function detailLines(d) {
    const lines = [];
    if (d.video_codec) {
      const profile = (d.video_profile || "").replace(/^h26\d_/, "").replace(/_/g, " ");
      const level = (d.video_level || "").replace(/^h26\d_/, "").replace(/_/g, ".");
      const v = [[codec(d.video_codec), profile && profile[0].toUpperCase() + profile.slice(1), level].filter(Boolean).join(" ")];
      if (d.video_width) v.push(`${d.video_width}×${d.video_height}`);
      if (d.video_fps) v.push(`${String(+d.video_fps.toFixed(2)).replace(".", ",")} i/s`);
      if (d.video_dar) v.push(d.video_dar.replace("_", ":"));
      if (d.video_pid != null) v.push(`PID ${d.video_pid}`);
      lines.push(["Image", v.join(" · ")]);
    }
    for (const a of d.audio || []) {
      const v = [codec(a.codec)];
      if (a.channels) v.push(`${a.channels} canaux`);
      if (a.sample_rate) v.push(`${(a.sample_rate / 1000).toLocaleString("fr-FR")} kHz`);
      if (a.bitrate) v.push(`${Math.round(a.bitrate / 1000)} kb/s`);
      if (a.pid) v.push(`PID ${a.pid}`);
      v.push(a.active ? "active" : "muette");
      lines.push([`Son, voie ${a.slot + 1}`, v.join(" · ")]);
    }
    const out = [];
    const sdi = d.sdi_formats || [];
    if (sdi[0]) out.push(`SDI 1-2 ${imageFormat(sdi[0])}`);
    if (sdi[1]) out.push(`SDI 3 ${imageFormat(sdi[1])}`);
    if (d.asi_descrambled != null) out.push(`ASI ${d.asi_descrambled ? "flux déchiffré" : "flux tel que reçu"}`);
    if (d.ip_streams != null) out.push(d.ip_streams ? `IP ${d.ip_streams} flux` : "IP coupée");
    if (d.genlock != null) out.push(`genlock ${d.genlock ? "oui" : "non"}`);
    if (d.loss_pattern) {
      const after = d.loss_freeze_ms ? ` après ${String(d.loss_freeze_ms / 1000).replace(".", ",")} s` : "";
      out.push(`sans image : ${PATTERNS[d.loss_pattern] || d.loss_pattern}${after}`);
    }
    if (out.length) lines.push(["Sorties (configuration)", out.join(" · ")]);
    return lines;
  }

  function range() {
    return live() ? Store.liveRange() : [data.from, data.to];
  }

  // Événements de la porteuse (ses IRD pendant leur appartenance, ses marqueurs) sur la période.
  function carrierEvents() {
    const all = live() ? Store.liveEvents() : [...data.events].sort((a, b) => (a.ts_end == null) - (b.ts_end == null) || b.ts_start - a.ts_start);
    return all.filter((e) => e.source === `porteuse:${carrier.key}`
      || (e.source?.startsWith("ird:") && carrier.irds.includes(e.source.slice(4)) && Charts.inPeriods(carrier, e.source.slice(4), e.ts_start)));
  }

  function onEventClick(ev) {
    const pad = Store.state.cfg.event_padding_s * 1000;
    go({ period: `${ev.ts_start - pad}-${(ev.ts_end ?? Date.now()) + pad}` });
  }

  function onSelect(from, to) {
    go({ period: `${from}-${to}` });
  }

  function rfReason() {
    const st = Store.state.states.fsh;
    if (live() && st === "down") return `Pas de niveau RF : aucune mesure du FSH4 (${Store.state.details.fsh || "injoignable"}).`;
    if (live() && st === "paused") return "Pas de niveau RF : FSH4 en pause.";
    return "Pas de niveau RF : aucun marqueur du FSH4 sur cette porteuse (hors du span, ou marqueur non placé).";
  }

  function renderHead() {
    const names = data.names;
    const pol = POL_LETTER[carrier.polarization] || "polarisation inconnue";
    const first = live() ? carrier.irds.map((s) => Store.state.latest[s]).find((m) => m?.modcod) : null;
    const meta = [`${fmtMhz(carrier.rf_khz)} MHz`, pol, carrier.symbol_rate ? `${num(carrier.symbol_rate / 1e6, 2)} MS/s` : "",
      first?.modcod || "", `${carrier.irds.length} IRD`, carrier.manual ? "saisie" : ""].filter(Boolean).join(" · ");
    const unknown = carrier.unknown_polarization || [];
    const sig = JSON.stringify([carrier.name, meta, unknown, unknown.map((s) => names[s])]);
    if (sig === sigs.head) return;
    sigs.head = sig;
    dom.title.textContent = carrier.name;
    dom.meta.textContent = meta;
    dom.polWarning.hidden = !unknown.length;
    dom.polWarning.replaceChildren(`Polarisation inconnue pour ${unknown.map((s) => names[s] || s).join(", ")} : à renseigner dans `,
      el("a", { href: routeHref({ page: "settings", tab: "appareils" }), textContent: "Réglages › Appareils" }), ".");
  }

  function destroyCharts() {
    if (!charts) return;
    for (const c of [charts.margin, charts.power, charts.band, charts.rf]) if (c) c.destroy();
    charts = null;
  }

  // Les graphes ne sont recréés que si les IRD, les marqueurs ou les noms changent.
  function buildCharts() {
    const names = data.names;
    const fsh = data.fsh;
    const sig = JSON.stringify([carrier.irds, carrier.markers, carrier.irds.map((s) => names[s]),
      carrier.markers.map((n) => fsh.freqs[n])]);
    if (charts && charts.sig === sig) return;
    destroyCharts();
    const ctx = () => ({ carrier, irds: data.irds, names: data.names, cfg: Store.state.cfg, live: live() });
    const [mCard, mZone] = Charts.chartCard("Marge", "dB · 0 = décrochage");
    const [pCard, pZone] = Charts.chartCard("Puissance d'entrée des IRD", "dBm");
    const [rCard, rZone] = Charts.chartCard("Niveau RF (FSH4)", "dBm");
    const [bCard, bZone] = Charts.chartCard("État", "une rangée par IRD");
    dom.charts.replaceChildren(mCard, pCard, rCard, bCard);
    charts = { sig };
    if (carrier.irds.length) {
      charts.margin = Charts.marginChart(mZone, carrier, names, { ctx, onSelect });
      charts.power = Charts.powerChart(pZone, carrier, names, { onSelect, height: 130 });
      charts.band = Charts.stateBand(bZone, carrier, names, { ctx, onSelect });
    } else {
      mCard.hidden = pCard.hidden = bCard.hidden = true;
    }
    if (carrier.markers.length) {
      charts.rf = Charts.markerChart(rZone, carrier.markers, fsh.freqs, { onSelect, height: 130 });
    } else {
      dom.rfReason = el("p", { className: "muted" });
      rZone.append(dom.rfReason);
    }
  }

  function renderCharts() {
    buildCharts();
    const [from, to] = range();
    if (charts.margin) {
      const d = Charts.irdData(data.irds, carrier);
      charts.margin.setData(d.margin);
      charts.power.setData(d.power);
      charts.band.setData(d.state);
    }
    if (charts.rf) charts.rf.setData(data.fsh);
    else dom.rfReason.textContent = rfReason();
    for (const c of [charts.margin, charts.power, charts.band, charts.rf]) if (c) c.setRange(from, to);
  }

  // Séries d'un IRD réduites à ses périodes d'appartenance à la porteuse.
  function ownSeries(serial) {
    const d = data.irds[serial];
    if (!d) return { ts: [], margin_db: [], state: [] };
    const keep = d.ts.map((t) => Charts.inPeriods(carrier, serial, t));
    const pick = (a) => a.filter((_, i) => keep[i]);
    return { ts: pick(d.ts), margin_db: pick(d.margin_min || d.margin_db), state: pick(d.state) };
  }

  function renderSections(events) {
    for (const [serial, sec] of sections) {
      if (!carrier.irds.includes(serial)) { sec.el.remove(); sections.delete(serial); }
    }
    dom.irds.style.gridTemplateColumns = `repeat(${Math.min(Math.max(carrier.irds.length, 1), 3)}, minmax(0, 1fr))`;
    const s = Store.state;
    carrier.irds.forEach((serial, i) => {
      let sec = sections.get(serial);
      if (!sec) {
        const card = Card.build(serial);
        card.el.classList.add("free");
        const details = el("dl", { className: "ird-details" });
        sec = { el: el("div", { className: "ird-section" }, card.el, details), card, details };
        sec.el.dataset.serial = serial;
        sec.el.style.setProperty("--ird-color", IRD_COLORS[i % IRD_COLORS.length]);
        sections.set(serial, sec);
      }
      if (dom.irds.children[i] !== sec.el) dom.irds.insertBefore(sec.el, dom.irds.children[i] || null);
      if (live()) {
        const down = s.states[`ird:${serial}`] === "down";
        const m = s.latest[serial];
        Card.update(sec.card, { latest: m, down, deviceState: s.states[`ird:${serial}`], series: s.irds[serial], carrier,
          openAlerts: Store.openEvents((e) => e.source === `ird:${serial}`).length, cfg: s.cfg, range: range() });
        const lines = m && !down && m.details ? detailLines(m.details) : [];
        const sig = JSON.stringify(lines);
        if (sec.details.dataset.sig !== sig) {
          sec.details.dataset.sig = sig;
          sec.details.replaceChildren(...lines.flatMap(([label, text]) => [el("dt", { textContent: label }), el("dd", { textContent: text })]));
        }
        sec.details.hidden = !lines.length;
      } else {
        const own = events.filter((e) => e.source === `ird:${serial}`);
        Card.updateSummary(sec.card, periodSummary(ownSeries(serial), own), { carrier });
        sec.details.hidden = true;
      }
    });
    if (!scrolled && route.ird && sections.has(route.ird)) {
      scrolled = true;
      const target = sections.get(route.ird).el;
      target.classList.add("target");
      requestAnimationFrame(() => target.scrollIntoView({ block: "center" }));
    }
  }

  function renderEvents(events) {
    const sig = JSON.stringify(events.map((e) => [e.id, e.ts_end, e.detail]));
    if (sig === sigs.events) return;
    sigs.events = sig;
    dom.events.replaceChildren(events.length ? eventList(events, { onClick: onEventClick, names: data.names })
      : emptyState("Aucun événement sur la période."));
  }

  function render() {
    if (!carrier) return;
    renderHead();
    renderCharts();
    const events = carrierEvents();
    renderSections(events);
    renderEvents(events);
  }

  // Direct : la porteuse est celle de la clé (ou la plus proche) parmi les porteuses actuelles.
  function onStore(kind) {
    if (!dom) return;
    data = Store.state;
    const found = matchCarrier(Store.state.carriers, { key: route.key });
    if (!found) {
      if (Store.state.carriers.length || kind !== "tick") notFound("Cette porteuse n'existe pas en ce moment.");
      return;
    }
    if (!carrier) showContent();
    carrier = found;
    dom.status.textContent = liveStatus();
    render();
  }

  function notFound(text) {
    carrier = null;
    sigs = {};  // en-tête et événements redessinés si la porteuse revient
    destroyCharts();
    dom.title.textContent = route.key;
    dom.meta.textContent = "";
    dom.content.hidden = true;
    dom.message.hidden = false;
    const links = [el("a", { href: routeHref({ page: "overview" }), textContent: "Vue d'ensemble" })];
    if (live()) links.push(" · ", el("a", { href: routeHref({ page: "carrier", key: route.key, period: "7j" }), textContent: "voir les 7 derniers jours" }));
    dom.message.replaceChildren(el("p", { textContent: text }), el("p", {}, ...links));
  }

  function showContent() {
    dom.message.hidden = true;
    dom.content.hidden = false;
  }

  async function loadPast() {
    const mine = ++seq;
    const [from, to] = periodRange(route.period, Date.now(), Store.state.cfg.live_window_min);
    dom.status.textContent = "chargement…";
    try {
      const width = dom.charts.clientWidth || 1000;
      const past = await Store.loadRange(from, to, Math.max(500, Math.round(width * 1.5)));
      if (mine !== seq || !dom) return;
      data = past;
      carrier = matchCarrier(past.carriers, { key: route.key });
      if (!carrier) {
        notFound("Aucune mesure de cette porteuse sur la période demandée.");
        dom.status.textContent = periodLabel(route.period, Store.state.cfg.live_window_min);
        return;
      }
      showContent();
      const gone = !matchCarrier(Store.state.carriers, { key: carrier.key });
      dom.notes.replaceChildren(...[
        gone ? el("p", { className: "banner", textContent: "Cette porteuse n'existe plus aujourd'hui : elle est affichée telle qu'elle était sur la période." }) : null,
        past.aggregated ? el("p", { className: "muted note", textContent: "Période longue : points regroupés (moyenne de chaque intervalle ; la marge minimale des résumés reste exacte)." }) : null,
      ].filter(Boolean));
      dom.status.textContent = isFixedPeriod(route.period) ? "" : periodLabel(route.period, Store.state.cfg.live_window_min);
      render();
    } catch (e) {
      if (mine !== seq || !dom) return;
      dom.status.textContent = "";
      dom.message.hidden = false;
      dom.content.hidden = true;
      dom.message.replaceChildren(el("p", { className: "error", textContent: `Chargement impossible : ${e.message}` }));
    }
  }

  function onResize() {
    if (charts) Charts.fitWidth([charts.margin, charts.power, charts.band, charts.rf].filter(Boolean));
  }

  function mount(view, r) {
    route = r;
    scrolled = false;
    dom = {
      title: el("h1"),
      meta: el("span", { className: "meta" }),
      status: el("span", { className: "meta" }),
      polWarning: el("p", { className: "banner", hidden: true }),
      notes: el("div", { className: "notes" }),
      charts: el("div", { className: "chart-stack" }),
      irds: el("div", { className: "ird-sections" }),
      events: el("div"),
      message: el("div", { className: "card empty", hidden: true }),
    };
    const period = PeriodSelector.build(route.period || "live", (p) => go({ period: p === "live" ? undefined : p }));
    const head = el("div", { className: "page-head" }, dom.title, dom.meta, el("div", { className: "actions" }, dom.status, period));
    const crumb = el("div", { className: "crumb" }, el("a", { href: routeHref({ page: "overview" }), textContent: "Vue d'ensemble" }), " › Porteuses");
    dom.content = el("div", { className: "carrier-main" },
      el("div", { className: "carrier-col" }, dom.charts, dom.irds),
      el("div", { className: "carrier-side" }, cardBox("Événements de la porteuse", dom.events)));
    view.append(el("div", { className: "page" }, crumb, head, dom.polWarning, dom.notes, dom.message, dom.content));
    dom.title.textContent = route.key;
    window.addEventListener("resize", onResize);
    if (live()) {
      onStore("reload");
      unsubscribe = Store.subscribe(onStore);
    } else {
      loadPast();
    }
  }

  function unmount() {
    seq++;
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    window.removeEventListener("resize", onResize);
    destroyCharts();
    sections = new Map();
    sigs = {};
    carrier = data = dom = route = null;
  }

  return { mount, unmount };
})();
