"use strict";

// Habillage unique des graphes (uPlot) : titre et unité dans la carte, légende sous la courbe, axe du temps en
// français, curseur synchronisé entre les graphes d'une même page, glisser pour choisir une période. Dessins
// propres à l'outil : seuils de marge, rangées de la bande « État ».

const Charts = (() => {
  const BAND_ROW_PX = 22;
  let alive = 0;  // graphes vivants (contrôle des fuites lors des navigations)

  // Axe du temps en français ; secondes sur les plages courtes, date sur les plages de plus d'un jour.
  function timeTicks(u, splits) {
    const span = u.scales.x.max - u.scales.x.min;
    const opts = { hour: "2-digit", minute: "2-digit", ...(span < 600 ? { second: "2-digit" } : {}) };
    return splits.map((s) => {
      const d = new Date(s * 1000);
      const t = d.toLocaleTimeString("fr-FR", opts);
      return span > 86_400 ? `${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })}\n${t}` : t;
    });
  }

  // Échelle d'au moins `span` unités autour des valeurs : les petites fluctuations ne remplissent pas le graphe.
  function minSpanRange(span) {
    return (u, min, max) => {
      if (min == null || max == null) return [-span, 0];
      const mid = (min + max) / 2, half = Math.max(span / 2, (max - min) / 2 + 1);
      return [Math.floor(mid - half), Math.ceil(mid + half)];
    };
  }

  function markerLabel(n, freqs) {
    const freq = freqs[n];
    const mhz = freq == null ? "" : ` ${(freq / 1e6).toFixed(1).replace(/\.0$/, "").replace(".", ",")} MHz`;
    return `M${n}${mhz}`;
  }

  // Options communes. extra : height, scales (ajoutées à l'axe du temps), yAxis, hooks, sync (clé de synchronisation
  // du curseur), onSelect(fromMs, toMs) (glisser sur le graphe).
  function opts(width, series, extra = {}) {
    const hooks = { ...(extra.hooks || {}) };
    if (extra.onSelect) {
      hooks.setSelect = [(u) => {
        if (u.select.width < 5) return;
        const min = u.posToVal(u.select.left, "x"), max = u.posToVal(u.select.left + u.select.width, "x");
        u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
        extra.onSelect(Math.round(min * 1000), Math.round(max * 1000));
      }];
    }
    return {
      width,
      height: extra.height ?? 160,
      cursor: { sync: { key: extra.sync || "page" }, drag: { x: !!extra.onSelect, y: false, setScale: false },
        bind: { dblclick: () => null } },
      scales: { x: { time: true }, ...(extra.scales || {}) },
      series: [{ label: "Heure", value: (u, v) => (v == null ? "—" : fmtDateTime(v * 1000)) }, ...series],
      axes: [{ values: timeTicks, stroke: "#6b7280", grid: { stroke: "#f0f1f3" }, ticks: { stroke: "#e3e6ea" } },
        { size: 56, stroke: "#6b7280", grid: { stroke: "#f0f1f3" }, ticks: { stroke: "#e3e6ea" }, ...(extra.yAxis || {}) }],
      legend: { live: true },
      hooks,
    };
  }

  // Carte contenant un graphe : titre, unité, zone du graphe ; renvoie [carte, zone].
  function chartCard(title, unit) {
    const zone = el("div", { className: "chart-zone" });
    const card = el("section", { className: "card chart-card" },
      el("div", { className: "card-title" }, el("span", { textContent: title }), el("span", { className: "unit", textContent: unit })), zone);
    return [card, zone];
  }

  // En historique, un IRD re-réglé n'appartient à la porteuse que pendant certaines périodes.
  function inPeriods(carrier, serial, t) {
    const spans = carrier.periods?.[serial];
    return !spans || spans.some(([from, to]) => t >= from && t <= to);
  }

  // Mesures de plusieurs IRD alignées sur un même axe de temps : [ts, { champ: [une colonne par IRD] }].
  function joinIrds(irds, carrier, fields) {
    const serials = carrier.irds;
    const ts = [...new Set(serials.flatMap((s) => (irds[s]?.ts || []).filter((t) => inPeriods(carrier, s, t))))]
      .sort((a, b) => a - b);
    const index = new Map(ts.map((t, i) => [t, i]));
    const cols = Object.fromEntries(fields.map((f) => [f, serials.map(() => new Array(ts.length).fill(null))]));
    serials.forEach((s, k) => {
      const d = irds[s];
      if (!d) return;
      d.ts.forEach((t, i) => {
        if (!index.has(t) || !inPeriods(carrier, s, t)) return;
        for (const f of fields) cols[f][k][index.get(t)] = d[f][i];
      });
    });
    return [ts, cols];
  }

  // Seuils de marge : minimum fixe (rouge, toujours) et, en Direct, seuil de chute rapide de chaque IRD (orange).
  function thresholdHook(getCtx) {
    return (u) => {
      const { carrier, irds, names, cfg, live } = getCtx();
      if (!carrier) return;
      const { left, top, width, height } = u.bbox;
      const ctx = u.ctx;
      ctx.save();
      ctx.lineWidth = 1.5;
      ctx.font = "11px -apple-system, sans-serif";
      ctx.textAlign = "right";
      const line = (level, color, dash, label) => {
        const y = u.valToPos(level, "y", true);
        if (y < top || y > top + height) return;
        ctx.strokeStyle = ctx.fillStyle = color;
        ctx.setLineDash(dash);
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(left + width, y);
        ctx.stroke();
        ctx.fillText(label, left + width - 4, y - 4);
      };
      if (cfg.margin_min_db > 0) line(cfg.margin_min_db, "#dc2626", [], `minimum ${fmt(cfg.margin_min_db, 1, "dB")}`);
      if (live) {
        for (const s of carrier.irds) {
          const level = dropThreshold(irds[s], cfg);
          if (!(level > 0)) continue;
          const who = carrier.irds.length > 1 ? `${names[s] || s} : ` : "";
          line(level, "#d97706", [6, 4], `${who}alerte si chute sous ${fmt(level, 1, "dB")}`);
        }
      }
      ctx.restore();
    };
  }

  // Bande « État » : une rangée par IRD, colorée selon le code de chaque mesure ; gris là où il n'y a pas de mesure.
  function stateHook(getCtx) {
    return (u) => {
      const { carrier, irds } = getCtx();
      if (!carrier || !carrier.irds.length) return;
      const { left, top, width, height } = u.bbox;
      const row = height / carrier.irds.length;
      const pad = Math.min(2, row / 6);
      const ctx = u.ctx;
      ctx.save();
      carrier.irds.forEach((s, k) => {
        const y = top + k * row + pad, h = row - 2 * pad;
        ctx.fillStyle = NO_DATA_COLOR;
        ctx.fillRect(left, y, width, h);
        const d = irds[s];
        if (!d || !d.ts.length) return;
        // Au-delà de cet écart entre deux mesures, le trou reste gris (coupure, pause).
        const step = d.ts.length > 1 ? (d.ts[d.ts.length - 1] - d.ts[0]) / (d.ts.length - 1) : 1000;
        const maxGap = Math.max(3000, 3 * step);
        for (let i = 0; i < d.ts.length; i++) {
          const code = d.state[i];
          if (code == null || !inPeriods(carrier, s, d.ts[i])) continue;
          let j = i;
          while (j + 1 < d.ts.length && d.state[j + 1] === code && d.ts[j + 1] - d.ts[j] <= maxGap
                 && inPeriods(carrier, s, d.ts[j + 1])) j++;
          const next = j + 1 < d.ts.length ? d.ts[j + 1] : Infinity;
          const end = next - d.ts[j] <= maxGap ? next : d.ts[j] + Math.min(step, 1000);
          const x0 = Math.max(u.valToPos(d.ts[i] / 1000, "x", true), left);
          const x1 = Math.min(u.valToPos(end / 1000, "x", true), left + width);
          ctx.fillStyle = STATE_COLORS[code] || NO_DATA_COLOR;
          if (x1 > x0) ctx.fillRect(x0, y, Math.max(x1 - x0, 1), h);
          i = j;
        }
      });
      ctx.restore();
    };
  }

  function bandLegend() {
    return el("p", { className: "band-legend" }, ...[0, 1, 2, 3, null].map((code) => {
      const swatch = el("i");
      swatch.style.background = code == null ? NO_DATA_COLOR : STATE_COLORS[code];
      return el("span", {}, swatch, code == null ? "pas de mesure" : STATE_LABELS[code]);
    }));
  }

  // Séries d'IRD (une couleur par IRD de la porteuse).
  function irdSeries(carrier, names, valueFmt) {
    return carrier.irds.map((s, k) => ({
      label: names[s] || s, stroke: IRD_COLORS[k % IRD_COLORS.length], width: 1.6, spanGaps: true, value: valueFmt,
    }));
  }

  // Graphe enveloppé : setData(données uPlot), setRange(début, fin en ms), destroy().
  function wrap(u) {
    alive++;
    let range = null;
    return {
      u,
      setData(data) {
        u.setData(data, !range);
        if (range) u.setScale("x", { min: range[0] / 1000, max: range[1] / 1000 });
      },
      setRange(from, to) {
        range = [from, to];
        u.setScale("x", { min: from / 1000, max: to / 1000 });
      },
      destroy() { alive--; u.destroy(); },
    };
  }

  function empty(n) {
    return [[], ...Array.from({ length: n }, () => [])];
  }

  // Marge de chaque IRD, échelle ancrée à 0 dB (décrochage) : elle se lit comme une réserve. ctx() → { carrier, irds,
  // names, cfg, live } pour les seuils.
  function marginChart(zone, carrier, names, { ctx, onSelect, height } = {}) {
    return wrap(new uPlot(opts(zone.clientWidth || 600, irdSeries(carrier, names, (u, v) => fmt(v, 1, "dB")), {
      height, onSelect, yAxis: { label: "dB" },
      scales: { y: { range: (u, min, max) => [0, Math.max(10, Math.ceil((max ?? 0) + 1))] } },
      hooks: { draw: [thresholdHook(ctx)] },
    }), empty(carrier.irds.length), zone));
  }

  // Puissance reçue par chaque IRD (le niveau RF quand il n'y a pas de FSH4).
  function powerChart(zone, carrier, names, { onSelect, height } = {}) {
    return wrap(new uPlot(opts(zone.clientWidth || 600, irdSeries(carrier, names, (u, v) => fmt(v, 0, "dBm")), {
      height, onSelect, yAxis: { label: "dBm" }, scales: { y: { range: minSpanRange(10) } },
    }), empty(carrier.irds.length), zone));
  }

  // Bande « État » : les séries ne servent qu'à la légende au survol ; les rangées sont dessinées à la main. L'axe de
  // gauche garde la largeur des autres graphes (curseurs alignés) et porte le nom des IRD. Légende des couleurs dessous.
  function stateBand(zone, carrier, names, { ctx, onSelect } = {}) {
    const n = carrier.irds.length;
    const labels = carrier.irds.map((s) => names[s] || s);
    const series = labels.map((label, k) => ({
      label, stroke: IRD_COLORS[k % IRD_COLORS.length], width: 0, paths: () => null, points: { show: false },
      value: (u, v) => (v == null ? "—" : STATE_LABELS[v] ?? "—"),
    }));
    const o = opts(zone.clientWidth || 600, series, {
      height: 64 + BAND_ROW_PX * n, onSelect, scales: { y: { range: [0, n] } },
      yAxis: { grid: { show: false }, ticks: { show: false }, splits: () => labels.map((_, k) => n - k - 0.5), values: () => labels },
      hooks: { drawClear: [stateHook(ctx)] },
    });
    o.axes[0] = { ...o.axes[0], grid: { show: false } };
    const chart = wrap(new uPlot(o, empty(n), zone));
    zone.append(bandLegend());
    return chart;
  }

  // Marqueurs FSH4 (une couleur par numéro), en dBm sur 10 dB au moins. setData(fsh) avec fsh = { ts, levels }.
  function markerChart(zone, nums, freqs, { onSelect, height, label = (n) => markerLabel(n, freqs) } = {}) {
    const series = nums.map((n) => ({ label: label(n), stroke: MARKER_COLORS[(n - 1) % 6], width: 1.6,
      spanGaps: true, value: (u, v) => fmt(v, 1, "dBm") }));
    const chart = wrap(new uPlot(opts(zone.clientWidth || 600, series, { height, onSelect, yAxis: { label: "dBm" },
      scales: { y: { range: minSpanRange(10) } } }), empty(nums.length), zone));
    const setData = chart.setData;
    chart.setData = (fsh) => setData([fsh.ts.map((t) => t / 1000), ...nums.map((n) => fsh.levels[n] || [])]);
    return chart;
  }

  // Données communes de la marge, de la puissance et de la bande : une colonne par IRD sur un même axe.
  function irdData(irds, carrier) {
    const [ts, cols] = joinIrds(irds, carrier, ["margin_db", "power_dbm", "state"]);
    const x = ts.map((t) => t / 1000);
    return { margin: [x, ...cols.margin_db], power: [x, ...cols.power_dbm], state: [x, ...cols.state] };
  }

  // Adapte la largeur des graphes à leur zone (fenêtre redimensionnée).
  function fitWidth(charts) {
    for (const c of charts) {
      const w = c.u.root.parentElement?.clientWidth;
      if (w && Math.abs(w - c.u.width) > 2) c.u.setSize({ width: w, height: c.u.height });
    }
  }

  return { chartCard, inPeriods, marginChart, powerChart, stateBand, markerChart, irdData, markerLabel, fitWidth,
    alive: () => alive };
})();

// Sélecteur de période : Direct, 1 h, 24 h, 7 j, Période… (deux champs date-heure). onChange(période).
const PeriodSelector = (() => {
  const CHOICES = [["live", "Direct"], ["1h", "1 h"], ["24h", "24 h"], ["7j", "7 j"]];

  function localInput(ms) {
    const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
    return d.toISOString().slice(0, 16);
  }

  function build(current, onChange, { choices = CHOICES } = {}) {
    const custom = !isLive(current) && !choices.some(([p]) => p === current);
    const seg = el("div", { className: "seg" });
    for (const [p, label] of choices) {
      const b = el("button", { type: "button", textContent: label,
        className: (p === "live" ? isLive(current) : p === current) ? "on" : "" });
      b.addEventListener("click", () => onChange(p));
      seg.append(b);
    }
    const toggle = el("button", { type: "button", textContent: "Période…", className: custom ? "on" : "" });
    seg.append(toggle);
    const [from, to] = periodRange(current, Date.now(), 60);
    const start = el("input", { type: "datetime-local", className: "input", value: localInput(custom ? from : Date.now() - 86_400_000) });
    const end = el("input", { type: "datetime-local", className: "input", value: localInput(custom ? to : Date.now()) });
    const apply = button("Afficher", () => {
      const a = new Date(start.value).getTime(), b = new Date(end.value).getTime();
      if (a < b) onChange(`${a}-${b}`);
      else alert("La fin de la période doit suivre son début.");
    }, { small: true, primary: true });
    const form = el("div", { className: "period-form" }, el("span", { textContent: "du" }), start,
      el("span", { textContent: "au" }), end, apply);
    form.hidden = !custom;
    toggle.addEventListener("click", () => { form.hidden = !form.hidden; });
    return el("div", { className: "period" }, seg, form);
  }

  return { build };
})();
