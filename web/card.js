"use strict";

// Carte d'IRD, la même partout (vue d'ensemble, page de porteuse) : état en clair, valeurs clés et aperçu de l'image
// décodée, courbe de marge avec ses seuils, bande « État ». Hors Direct, variante « résumé de la période ». Et la grille qui adapte la taille
// des cartes à leur nombre.

const Card = (() => {
  const STALE_MS = 10_000;
  // Carte de référence (échelle 1), complète puis compacte ; sous l'échelle minimale de la compacte, la page défile.
  const BASE_W = 300, GAP = 12, MAX_SCALE = 2.2;
  const FULL = { h: 290, min: 0.85 }, COMPACT = { h: 195, min: 0.75 };

  // Depuis quand la rangée « État » garde son code actuel ; au-delà de la fenêtre affichée : « plus de N min ».
  function since(d, now, from, windowMin) {
    let i = d.state.length - 1;
    const code = d.state[i];
    while (i > 0 && d.state[i - 1] === code && d.ts[i] - d.ts[i - 1] <= 3000) i--;
    if (i === 0 && d.ts[0] <= from + 5000) return `depuis plus de ${windowMin} min`;
    const s = Math.round((now - d.ts[i]) / 1000);
    return s < 60 ? `depuis ${s} s` : `depuis ${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  }

  function fmtDuration(ms) {
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
    return `${Math.floor(s / 3600)} h ${String(Math.floor(s / 60) % 60).padStart(2, "0")} min`;
  }

  function build(serial, { onClick, hint = "" } = {}) {
    const h = { serial, el: el("article", { className: "ird-card", title: hint }) };
    h.hint = hint;
    h.dot = el("span", { className: "dot" });
    h.name = el("strong", { className: "ird-name" });
    h.badge = el("span", { className: "badge" });
    h.why = el("p", { className: "ird-why" });
    h.sub = el("p", { className: "ird-sub" });
    h.values = el("dl", { className: "ird-values" });
    h.thumbImg = el("img", { alt: "" });
    h.thumbCap = el("figcaption");
    h.thumb = el("figure", { className: "ird-thumb none" }, h.thumbImg, h.thumbCap);
    h.thumbTs = undefined;  // heure de la dernière image annoncée (chargée une seule fois)
    h.thumbShown = null;    // heure de l'image affichée : une image qui n'a pas pu être chargée laisse la précédente
    h.thumb.addEventListener("click", (e) => {
      if (h.thumbShown == null) return;  // cadre vide : le clic reste celui de la tuile (ouvrir la porteuse)
      e.stopPropagation();  // la tuile mène à la porteuse ; l'image, elle, s'agrandit sur place
      enlarge(h);
    });
    h.chartTitle = el("p", { className: "ird-chart-title" });
    h.spark = el("canvas", { className: "ird-spark" });
    h.band = el("canvas", { className: "ird-band" });
    h.since = el("span");
    const axis = el("p", { className: "ird-axis" }, h.since, el("span", { textContent: "maintenant" }));
    h.chart = el("div", { className: "ird-chart" }, h.chartTitle, h.spark, h.band, axis);
    h.el.append(el("header", {}, h.dot, h.name, h.badge), h.why, h.sub,
      el("div", { className: "ird-top" }, h.values, h.thumb), h.chart);
    if (onClick) {
      // Atteignable au clavier seulement si elle mène quelque part (Entrée ou Espace).
      h.el.classList.add("link");
      h.el.tabIndex = 0;
      h.el.setAttribute("role", "link");
      h.el.addEventListener("click", () => onClick(serial));
      h.el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(serial);
        }
      });
    }
    return h;
  }

  // Une seule fenêtre d'agrandissement pour toute la page, à la taille réelle de l'aperçu (320×180) ; elle suit les
  // images de sa carte tant qu'elle est ouverte.
  let viewer = null;
  function enlarge(h) {
    if (!viewer) {
      const img = el("img", { alt: "" });
      const cap = el("p");
      viewer = { dialog: el("dialog", { className: "thumb-viewer" }, img, cap), img, cap, card: null };
      viewer.dialog.addEventListener("click", () => viewer.dialog.close());
      document.body.append(viewer.dialog);
    }
    viewer.card = h;
    syncViewer(h);
    if (!viewer.dialog.open) viewer.dialog.showModal();
  }

  function syncViewer(h) {
    if (viewer?.card !== h) return;
    if (viewer.img.src !== h.thumbImg.src) viewer.img.src = h.thumbImg.src;
    viewer.img.className = h.thumb.classList.contains("old") ? "old" : "";
    viewer.cap.textContent = `${h.name.textContent} · ${h.thumbCap.textContent}`;
  }

  function updateThumb(h, m) {
    const ts = m?.thumb_ts ?? null;
    if (ts !== h.thumbTs) {
      h.thumbTs = ts;
      if (ts == null) {
        h.thumbShown = null;
        h.thumbImg.removeAttribute("src");
      } else {
        // Chargée à part, affichée une fois arrivée : pas de cadre vide entre deux images, ni sur un échec.
        const next = new Image();
        next.onload = () => {
          if (h.thumbTs !== ts) return;  // une plus récente est déjà annoncée
          h.thumbImg.src = next.src;
          h.thumbShown = ts;
        };
        next.src = `/api/ird/${encodeURIComponent(h.serial)}/image.jpg?t=${ts}`;
      }
    }
    const shown = h.thumbShown;
    const view = thumbView(shown, Date.now(), Store.state.connected);
    h.thumb.className = `ird-thumb ${view.state}`;
    h.thumbCap.textContent = shown == null ? view.prefix : view.prefix + fmtTime(shown);
    h.thumb.title = shown == null ? "" : "Agrandir l'image";
    h.thumb.hidden = false;
    syncViewer(h);
  }

  function fit(canvas) {
    const ratio = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * ratio), h = Math.round(canvas.clientHeight * ratio);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    return [canvas.getContext("2d"), w, h, ratio];
  }

  // Marge sur la fenêtre affichée, échelle partant de 0 dB (décrochage). Seuils : chute rapide (orange, pointillé)
  // et minimum fixe (rouge). En compact, ni graduations ni texte : la courbe et les lignes seulement.
  function drawSpark(canvas, d, range, drop, minimum, scale, compact) {
    const [ctx, w, h, ratio] = fit(canvas);
    ctx.clearRect(0, 0, w, h);
    const [from, to] = range;
    const vals = d ? d.margin_db.filter((v, i) => v != null && d.ts[i] >= from) : [];
    const top = Math.max(10, Math.ceil(Math.max(0, minimum || 0, ...vals) + 1));
    const font = Math.round(10 * scale * ratio);
    const pad = compact ? 2 * ratio : Math.ceil(font / 2) + ratio;
    const left = compact ? 0 : 30 * ratio * scale;
    const x = (t) => left + ((t - from) / (to - from)) * (w - left);
    const y = (v) => h - pad - (v / top) * (h - 2 * pad);
    ctx.font = `${font}px -apple-system, sans-serif`;
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    ctx.strokeStyle = "#e3e6ea";
    ctx.lineWidth = ratio;
    for (const v of [0, top]) {
      ctx.fillStyle = "#6b7280";
      if (!compact) ctx.fillText(`${v} dB`, left - 4 * ratio, y(v));
      ctx.beginPath();
      ctx.moveTo(left, y(v));
      ctx.lineTo(w, y(v));
      ctx.stroke();
    }
    const line = (level, color, dash, label) => {
      ctx.strokeStyle = ctx.fillStyle = color;
      ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(left, y(level));
      ctx.lineTo(w, y(level));
      ctx.stroke();
      ctx.setLineDash([]);
      if (compact) return;
      ctx.textAlign = "left";
      const above = y(level) - font - 2 * ratio >= 0;  // libellé au-dessus de la ligne, sinon dessous
      ctx.textBaseline = above ? "bottom" : "top";
      ctx.fillText(label, left + 4 * ratio, y(level) + (above ? -2 : 2) * ratio);
    };
    if (minimum > 0) line(minimum, "#dc2626", [], `minimum ${fmt(minimum, 1, "dB")}`);
    if (drop != null && drop > 0) line(drop, "#d97706", [4 * ratio, 3 * ratio], "alerte si chute");
    if (!d) return;
    ctx.strokeStyle = "#3341c4";
    ctx.lineWidth = 1.5 * ratio;
    ctx.beginPath();
    let pen = false, prev = null;
    for (let i = 0; i < d.ts.length; i++) {
      const v = d.margin_db[i], t = d.ts[i];
      if (t < from || v == null || (prev != null && t - prev > 5000)) pen = false;
      if (t >= from && v != null) {
        if (pen) ctx.lineTo(x(t), y(v)); else ctx.moveTo(x(t), y(v));
        pen = true;
      }
      prev = t;
    }
    ctx.stroke();
  }

  // Bande « État » sur la même largeur que la courbe (même décalage à gauche).
  function drawBand(canvas, d, range, scale, compact) {
    const [ctx, w, h, ratio] = fit(canvas);
    ctx.clearRect(0, 0, w, h);
    const left = compact ? 0 : 30 * ratio * scale;
    ctx.fillStyle = NO_DATA_COLOR;
    ctx.fillRect(left, 0, w - left, h);
    if (!d) return;
    const [from, to] = range;
    const x = (t) => left + Math.max(0, ((t - from) / (to - from)) * (w - left));
    for (let i = 0; i < d.ts.length; i++) {
      const code = d.state[i];
      if (code == null || d.ts[i] < from - 1000) continue;
      const next = i + 1 < d.ts.length && d.ts[i + 1] - d.ts[i] <= 3000 ? d.ts[i + 1] : d.ts[i] + 1000;
      ctx.fillStyle = STATE_COLORS[code] || NO_DATA_COLOR;
      ctx.fillRect(x(d.ts[i]), 0, Math.max(x(next) - x(d.ts[i]), 1), h);
    }
  }

  // Remplace le contenu d'un élément seulement s'il a changé (pas de reconstruction du DOM à chaque seconde).
  function setRows(node, rows) {
    const sig = JSON.stringify(rows);
    if (node.dataset.sig === sig) return;
    node.dataset.sig = sig;
    node.replaceChildren(...rows.map(([k, v, hint, tone]) => {
      const dd = el("dd", { textContent: v, className: tone || "" });
      if (hint) dd.append(el("small", { textContent: hint }));
      dd.title = [v, hint].filter(Boolean).join(" · ");  // texte complet au survol : à côté de l'aperçu, il peut être coupé
      return el("div", {}, el("dt", { textContent: k }), dd);
    }));
  }

  function setTone(h, code) {
    const tone = typeof code === "number" ? TONE_OF_CODE[code] : TONE_OF_CODE[code] ?? "";
    h.el.dataset.tone = tone || "idle";
    h.badge.className = `badge ${tone}`;
  }

  // Direct. data : { latest, down, series, carrier, openAlerts, cfg, range, scale, compact, deviceState }
  function update(h, data) {
    const { latest: m, down, series: d, carrier, openAlerts = 0, cfg, range, scale = 1, compact = false } = data;
    const minimum = cfg.margin_min_db;
    // Direct coupé, ou plus de mesure depuis STALE_MS : la dernière mesure n'est plus l'état de l'IRD.
    const seen = Store.state.seen[`ird:${h.serial}`];
    const stale = !Store.state.connected || (seen != null && Date.now() - seen > STALE_MS);
    const [title, why, code] = irdStatus(m, down, minimum, stale);
    setTone(h, code);
    h.dot.className = `dot ${{ up: "ok", down: "bad" }[data.deviceState] || ""}`;
    h.dot.title = [(DEVICE_STATE[data.deviceState] || ["en attente"])[0],
      data.deviceState === "down" ? Store.state.details[`ird:${h.serial}`] : "",
      seen ? `dernière mesure ${fmtTime(seen)}` : "aucune mesure"].filter(Boolean).join(" · ");
    h.name.textContent = Store.state.names[h.serial] || h.serial;
    h.badge.textContent = title;
    const lastCode = d?.state.length ? d.state[d.state.length - 1] : null;
    const cause = title === "OK" ? "" : [why, code === lastCode ? since(d, Date.now(), range[0], cfg.live_window_min) : ""].filter(Boolean).join(" — ");
    h.why.textContent = [cause, openAlerts ? `${openAlerts} alerte${openAlerts > 1 ? "s" : ""} en cours` : ""].filter(Boolean).join(" · ");
    h.el.title = [h.why.textContent, h.hint].filter(Boolean).join("\n");
    h.sub.textContent = [m?.service_name, carrier ? `porteuse ${carrier.name}` : null].filter(Boolean).join(" · ") || "—";
    const live = m && !down && !stale;
    const thr = dropThreshold(d, cfg);
    const limits = [thr > 0 ? `chute si < ${fmt(thr, 1, "dB")}` : "", minimum > 0 ? `min. ${fmt(minimum, 1, "dB")}` : ""]
      .filter(Boolean).join(" · ");
    setRows(h.values, [
      ["Marge", live ? fmt(m.margin_db, 1, "dB") : "—", (live && limits) || "réserve avant décrochage"],
      ["Signal reçu", live && m.power_dbm != null ? fmt(m.power_dbm, 0, "dBm") : "—", "puissance d'entrée"],
      [compact ? "Temp." : "Température", live && m.health?.fpga_temp != null ? `${Math.round(m.health.fpga_temp)} °C` : "—", "FPGA"],
    ]);
    updateThumb(h, m);
    h.chart.hidden = false;
    h.chartTitle.textContent = `Marge sur ${cfg.live_window_min} min`;
    h.since.textContent = `il y a ${cfg.live_window_min} min`;
    drawSpark(h.spark, d, range, thr, minimum, scale, compact);
    drawBand(h.band, d, range, scale, compact);
  }

  // Hors Direct : résumé de la période (aucune donnée de décodage n'est gardée dans le temps).
  function updateSummary(h, summary, { program = "", carrier = null } = {}) {
    const worst = [3, 2, 1].find((c) => summary.state_ms[c] > 0);
    setTone(h, worst ?? (summary.margin_min == null ? "idle" : 0));
    h.dot.className = "dot";
    h.name.textContent = Store.state.names[h.serial] || h.serial;
    h.badge.textContent = summary.margin_min == null ? "PAS DE MESURE" : worst == null ? "SANS DÉFAUT" : "DÉFAUTS SUR LA PÉRIODE";
    h.why.textContent = summary.alerts ? `${summary.alerts} alerte${summary.alerts > 1 ? "s" : ""} sur la période` : "";
    h.sub.textContent = [program, carrier ? `porteuse ${carrier.name}` : null].filter(Boolean).join(" · ") || "Résumé de la période";
    setRows(h.values, [
      ["Marge minimale", fmt(summary.margin_min, 1, "dB"), ""],
      ["Lock perdu", fmtDuration(summary.state_ms[3]), "", summary.state_ms[3] ? "bad" : ""],
      ["Image ou flux", fmtDuration(summary.state_ms[2]), "perdu", summary.state_ms[2] ? "violet" : ""],
      ["BER, son, marge", fmtDuration(summary.state_ms[1]), "dégradé", summary.state_ms[1] ? "warn" : ""],
    ]);
    h.chart.hidden = true;
    h.thumb.hidden = true;  // aucune image n'est gardée : rien à montrer pour une période passée
  }

  // Colonnes et échelle qui remplissent au mieux la place disponible pour n cartes d'une hauteur de référence.
  function fill(n, width, height, baseH) {
    let best = { cols: 1, scale: 0, tileH: baseH };
    for (let cols = 1; cols <= Math.max(1, n); cols++) {
      const rows = Math.ceil(n / cols);
      const w = (width - GAP * (cols - 1)) / cols;
      const h = (height - GAP * (rows - 1)) / rows;
      const scale = Math.min(w / BASE_W, h / baseH);
      if (scale > best.scale) best = { cols, scale, tileH: h };
    }
    best.scale = Math.min(MAX_SCALE, best.scale);
    best.tileH = Math.min(best.tileH, baseH * best.scale * 1.15);
    return best;
  }

  // Cartes complètes si elles tiennent, sinon compactes ; sinon taille minimale et la page défile.
  function layout(n, width, height) {
    const full = fill(n, width, height, FULL.h);
    if (full.scale >= FULL.min) return { ...full, compact: false };
    const compact = fill(n, width, height, COMPACT.h);
    if (compact.scale >= COMPACT.min) return { ...compact, compact: true };
    const cols = Math.max(1, Math.floor((width + GAP) / (BASE_W * COMPACT.min + GAP)));
    return { cols, scale: COMPACT.min, tileH: COMPACT.h * COMPACT.min, compact: true };
  }

  // Applique une disposition à la grille (colonnes, échelle --s, hauteur des cartes).
  function applyLayout(grid, { cols, scale, tileH, compact }) {
    grid.classList.toggle("compact", compact);
    grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    grid.style.setProperty("--s", scale.toFixed(3));
    grid.style.setProperty("--card-h", `${Math.floor(tileH)}px`);
  }

  return { build, update, updateSummary, layout, applyLayout };
})();
