"use strict";

// Tuile d'IRD, la même partout (vue d'ensemble, page de porteuse), disposition « chiffre d'abord » : nom et état,
// cause en clair si l'état n'est pas OK, marge en grand avec l'image décodée à côté, courbe de marge sur toute la
// largeur. Hors Direct, variante « résumé de la période ». Les tailles suivent celle de la tuile (CSS, unités de
// conteneur) ; la grille de la vue d'ensemble est choisie par tileGrid (core.js).

const Card = (() => {
  const STALE_MS = 10_000;

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
    h.name = el("strong", { className: "ird-name" });
    h.badge = el("span", { className: "badge" });
    h.why = el("p", { className: "ird-why" });
    h.sub = el("p", { className: "ird-sub" });
    h.numLabel = el("span", { className: "ird-num-label" });
    h.numValue = el("span", { className: "ird-num-value" });
    h.num = el("div", { className: "ird-num" }, h.numLabel, el("span", { className: "ird-num-line" }, h.numValue,
      el("span", { className: "ird-num-unit", textContent: "dB" })));
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
    h.spark = el("canvas", { className: "ird-spark" });
    h.band = el("canvas", { className: "ird-band" });
    h.chart = el("div", { className: "ird-chart" }, h.spark, h.band);
    h.durations = el("dl", { className: "ird-durations", hidden: true });
    h.el.append(el("header", {}, h.name, h.badge), h.why, h.sub, h.num, h.thumb, h.chart, h.durations);
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
    // Image à jour : pas d'heure (elle n'apprend rien) ; ancienne : « image de 10:41 » ; aucune : « pas d'image ».
    h.thumbCap.textContent = view.state === "fresh" ? "" : shown == null ? view.prefix : view.prefix + fmtTime(shown).slice(0, 5);
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

  // Marge sur la fenêtre affichée, échelle partant de 0 dB (décrochage), sur toute la largeur. Seuils : chute rapide
  // (orange, pointillé) et minimum fixe (rouge), sans texte ni graduation : la valeur est le grand chiffre, le détail
  // est sur la page de la porteuse.
  function drawSpark(canvas, d, range, drop, minimum) {
    const [ctx, w, h, ratio] = fit(canvas);
    ctx.clearRect(0, 0, w, h);
    if (!w || !h) return;
    const [from, to] = range;
    const vals = d ? d.margin_db.filter((v, i) => v != null && d.ts[i] >= from) : [];
    const top = Math.max(10, Math.ceil(Math.max(0, minimum || 0, ...vals) + 1));
    const pad = 2 * ratio;
    const x = (t) => ((t - from) / (to - from)) * w;
    const y = (v) => h - pad - (v / top) * (h - 2 * pad);
    ctx.lineWidth = ratio;
    ctx.strokeStyle = "#e3e6ea";
    ctx.beginPath();
    ctx.moveTo(0, y(0));
    ctx.lineTo(w, y(0));
    ctx.stroke();
    const lines = [];
    if (minimum > 0) lines.push([minimum, "#dc2626", []]);
    if (drop != null && drop > 0) lines.push([drop, "#d97706", [4 * ratio, 3 * ratio]]);
    for (const [level, color, dash] of lines) {
      ctx.strokeStyle = color;
      ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(0, y(level));
      ctx.lineTo(w, y(level));
      ctx.stroke();
    }
    ctx.setLineDash([]);
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

  // Bande « État » sous la courbe, même largeur : un trait coloré seconde par seconde, gris sans mesure.
  function drawBand(canvas, d, range) {
    const [ctx, w, h] = fit(canvas);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = NO_DATA_COLOR;
    ctx.fillRect(0, 0, w, h);
    if (!d) return;
    const [from, to] = range;
    const x = (t) => Math.max(0, ((t - from) / (to - from)) * w);
    for (let i = 0; i < d.ts.length; i++) {
      const code = d.state[i];
      if (code == null || d.ts[i] < from - 1000) continue;
      const next = i + 1 < d.ts.length && d.ts[i + 1] - d.ts[i] <= 3000 ? d.ts[i + 1] : d.ts[i] + 1000;
      ctx.fillStyle = STATE_COLORS[code] || NO_DATA_COLOR;
      ctx.fillRect(x(d.ts[i]), 0, Math.max(x(next) - x(d.ts[i]), 1), h);
    }
  }

  // Remplace le contenu d'une liste seulement s'il a changé (pas de reconstruction du DOM à chaque seconde).
  function setRows(node, rows) {
    const sig = JSON.stringify(rows);
    if (node.dataset.sig === sig) return;
    node.dataset.sig = sig;
    node.replaceChildren(...rows.map(([k, v, tone]) => el("div", {}, el("dt", { textContent: k }),
      el("dd", { textContent: v, className: tone || "" }))));
  }

  function setTone(h, code) {
    const tone = typeof code === "number" ? TONE_OF_CODE[code] : TONE_OF_CODE[code] ?? "";
    h.el.dataset.tone = tone || "idle";
    h.badge.className = `badge ${tone}`;
  }

  // Direct. data : { latest, down, deviceState, series, cfg, range }
  function update(h, data) {
    const { latest: m, down, series: d, cfg, range } = data;
    const minimum = cfg.margin_min_db;
    // Direct coupé, ou plus de mesure depuis STALE_MS : la dernière mesure n'est plus l'état de l'IRD.
    const seen = Store.state.seen[`ird:${h.serial}`];
    const stale = !Store.state.connected || (seen != null && Date.now() - seen > STALE_MS);
    const [title, why, code] = irdStatus(m, down, minimum, stale);
    setTone(h, code);
    h.name.textContent = Store.state.names[h.serial] || h.serial;
    h.badge.textContent = title;
    h.badge.title = [(DEVICE_STATE[data.deviceState] || ["en attente"])[0],
      data.deviceState === "down" ? Store.state.details[`ird:${h.serial}`] : "",
      seen ? `dernière mesure ${fmtTime(seen)}` : "aucune mesure"].filter(Boolean).join(" · ");
    const lastCode = d?.state.length ? d.state[d.state.length - 1] : null;
    const cause = title === "OK" ? "" : [why, code === lastCode ? since(d, Date.now(), range[0], cfg.live_window_min) : ""]
      .filter(Boolean).join(" — ");
    h.why.textContent = cause ? cause[0].toUpperCase() + cause.slice(1) : "";
    h.sub.hidden = true;  // programme et porteuse : au clic (page de la porteuse)
    h.el.title = [h.why.textContent, h.hint].filter(Boolean).join("\n");
    const live = m && !down && !stale;
    h.numLabel.textContent = "Marge";
    h.numValue.textContent = live && m.margin_db != null ? num(m.margin_db, 1) : "—";
    h.num.className = `ird-num ${marginTone(m?.margin_db, minimum, live)}`;
    h.durations.hidden = true;
    updateThumb(h, m);
    h.chart.hidden = false;
    drawSpark(h.spark, d, range, dropThreshold(d, cfg), minimum);
    drawBand(h.band, d, range);
  }

  // Hors Direct : résumé de la période (aucune donnée de décodage n'est gardée dans le temps).
  function updateSummary(h, summary, { program = "", carrier = null } = {}) {
    const worst = [3, 2, 1].find((c) => summary.state_ms[c] > 0);
    setTone(h, worst ?? (summary.margin_min == null ? "idle" : 0));
    h.name.textContent = Store.state.names[h.serial] || h.serial;
    h.badge.textContent = summary.margin_min == null ? "PAS DE MESURE" : worst == null ? "SANS DÉFAUT" : "DÉFAUTS";
    h.badge.title = "";
    h.why.textContent = summary.alerts ? `${summary.alerts} alerte${summary.alerts > 1 ? "s" : ""} sur la période` : "";
    h.sub.hidden = false;
    h.sub.textContent = [program, carrier ? `porteuse ${carrier.name}` : null].filter(Boolean).join(" · ") || "Résumé de la période";
    h.numLabel.textContent = "Marge min.";
    h.numValue.textContent = summary.margin_min == null ? "—" : num(summary.margin_min, 1);
    h.num.className = "ird-num";
    h.durations.hidden = false;
    setRows(h.durations, [
      ["Lock perdu", fmtDuration(summary.state_ms[3]), summary.state_ms[3] ? "bad" : ""],
      ["Image ou flux perdu", fmtDuration(summary.state_ms[2]), summary.state_ms[2] ? "violet" : ""],
      ["Dégradé", fmtDuration(summary.state_ms[1]), summary.state_ms[1] ? "warn" : ""],
    ]);
    h.chart.hidden = true;
    h.thumb.hidden = true;  // aucune image n'est gardée : rien à montrer pour une période passée
  }

  // Grille de la vue d'ensemble : colonnes et hauteur des tuiles (tileGrid, core.js).
  function layout(n, width, height) {
    return tileGrid(n, width, height);
  }

  function applyLayout(grid, { cols, tileH }) {
    grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    grid.style.setProperty("--card-h", `${Math.floor(tileH)}px`);
  }

  return { build, update, updateSummary, layout, applyLayout };
})();
