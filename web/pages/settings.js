"use strict";

// Réglages en pages : Appareils (FSH4, IRD suivis, ajout, recherche sur le réseau), Porteuses (noms, porteuses
// saisies), Seuils d'alerte. Chaque erreur s'affiche sous le bloc de l'action concernée.

const SettingsPage = (() => {
  const TITLES = { appareils: "Appareils", porteuses: "Porteuses", seuils: "Seuils d'alerte" };
  const POL_OPTIONS = [["vertical", "verticale"], ["horizontal", "horizontale"]];
  let route = null;
  let dom = null;
  let unsubscribe = null;
  let seq = 0;

  const enc = encodeURIComponent;

  function errorBox() {
    return el("p", { className: "error", hidden: true });
  }

  function showError(box, message) {
    box.textContent = message;
    box.hidden = !message;
  }

  function select(options, value, extra = {}) {
    const s = el("select", { className: "input", ...extra });
    for (const [v, label] of options) s.append(el("option", { value: v, textContent: label, selected: (value || "") === v }));
    return s;
  }

  function field(label, input, help = "") {
    const f = el("label", { className: "field" }, label, input);
    if (help) f.append(el("small", { textContent: help }));
    return f;
  }

  // Action sur le service : erreur affichée dans `box`, puis page et données partagées rafraîchies.
  async function act(fn, box) {
    showError(box, "");
    try {
      await fn();
    } catch (e) {
      showError(box, e.message);
    }
    await refresh();
    Store.reload();
  }

  function carrierName(key) {
    return Store.state.carriers.find((c) => c.key === key)?.name || key || "—";
  }

  // ----- Appareils -----

  // Carte du FSH4. Tant que le formulaire a des modifications non enregistrées, il est gardé tel quel d'un
  // rafraîchissement à l'autre (seuls l'état et le bouton de pause sont redessinés).
  function fshCard(fsh) {
    const err = dom.fshErr ??= errorBox();  // gardée d'un rafraîchissement à l'autre
    if (!dom.fshDirty || !dom.fshForm) dom.fshForm = fshForm(fsh, err);
    const paused = Store.state.states.fsh === "paused";
    const pause = button(paused ? "Reprendre les mesures" : "Mettre en pause", () => toggleFshPause((m) => showError(err, m)), { small: true });
    const [label, tone] = DEVICE_STATE[Store.state.states.fsh] || DEVICE_STATE.unknown;
    const seen = Store.state.seen.fsh ? ` · dernière mesure ${fmtDateTime(Store.state.seen.fsh)}` : "";
    return cardBox(el("div", { className: "card-title" }, el("span", {}, "FSH4 ", el("span", { className: `badge ${tone}`, textContent: label }),
      el("small", { className: "muted", textContent: seen })), pause),
      el("p", { className: "muted note", textContent: "Le service lit les réglages du FSH4 sans jamais les modifier." }), dom.fshForm, err);
  }

  function fshForm(fsh, err) {
    const host = el("input", { className: "input", value: fsh.host, required: true, spellcheck: false });
    const port = el("input", { className: "input", type: "number", min: 1, max: 65535, value: fsh.port, required: true });
    const pol = select([["", "inconnue"], ...POL_OPTIONS], fsh.polarization);
    const form = el("form", { className: "form-row" }, field("Adresse", host), field("Port", port), field("Entrée (polarisation)", pol),
      el("button", { type: "submit", className: "btn primary", textContent: "Enregistrer" }));
    form.addEventListener("input", () => { dom.fshDirty = true; });
    form.addEventListener("change", () => { dom.fshDirty = true; });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      dom.fshDirty = false;
      act(() => Store.post("/api/fsh", { host: host.value.trim(), port: Number(port.value), polarization: pol.value || null }), err);
    });
    dom.fshHost = host;
    dom.fshPort = port;
    return form;
  }

  function irdRow(ird, err) {
    const url = `/api/irds/${enc(ird.serial)}`;
    const name = el("input", { className: "input", value: ird.name });
    name.addEventListener("change", () => act(() => Store.post(url, { name: name.value }), err));
    const host = el("input", { className: "input", value: ird.host, spellcheck: false });
    host.addEventListener("change", () => act(() => Store.post(url, { host: host.value.trim() }), err));
    // Polarisation : imposée ici, ou automatique (réglage de l'IRD, valable seulement s'il alimente le LNB).
    const auto = ird.polarization ? "automatique" : `automatique (${POL_NAME[ird.effective_polarization] || "inconnue"})`;
    const pol = select([["", auto], ...POL_OPTIONS], ird.polarization);
    pol.addEventListener("change", () => act(() => Store.post(url, { polarization: pol.value || null }), err));
    const remove = button("Retirer", () => {
      if (confirm(`Retirer ${ird.name} de la surveillance ? Son historique est conservé.`)) act(() => Store.post(`${url}/remove`), err);
    }, { small: true });
    const tone = { up: "ok", down: "bad" }[ird.state] || "";
    return el("tr", {}, el("td", {}, name), el("td", {}, host), el("td", {}, pol), el("td", { textContent: carrierName(ird.carrier) }),
      el("td", { title: `n° ${ird.serial}` }, el("span", { className: `badge ${tone}`, textContent: (DEVICE_STATE[ird.state] || [ird.state])[0] }),
        el("small", { className: "muted seen", textContent: Store.state.seen[`ird:${ird.serial}`]
          ? ` ${fmtTime(Store.state.seen[`ird:${ird.serial}`])}` : "", title: "heure de la dernière mesure" })),
      el("td", {}, remove));
  }

  function foundList(title, items, describe, action) {
    const rows = items.map((item) => {
      const [main, sub, label] = describe(item);
      const btn = button(label || "Déjà suivi", () => action(item), { small: true });
      btn.disabled = !label;
      return el("li", { className: "found" }, el("div", {}, el("strong", { textContent: main }), el("small", { className: "muted", textContent: sub })), btn);
    });
    return [el("h3", { textContent: title }), el("ul", { className: "found-list" }, ...(rows.length ? rows : [el("li", { className: "muted", textContent: "Aucun trouvé." })]))];
  }

  async function scan(btn, info, found, err) {
    btn.disabled = true;
    showError(err, "");
    info.textContent = "Recherche en cours…";
    try {
      const [r, d] = await Promise.all([Store.post("/api/discover"), Store.getJSON("/api/devices")]);
      if (!dom) return;
      const known = new Set(d.irds.map((i) => i.serial));
      info.textContent = `Réseaux parcourus : ${r.networks.join(", ") || "aucun"}`;
      found.replaceChildren(
        ...foundList("Analyseurs FSH", r.fsh, (f) => [`${f.model} — ${f.host}`, f.detail, "Utiliser"], (f) => {
          dom.fshHost.value = f.host;
          dom.fshPort.value = f.port;
          dom.fshDirty = true;
          dom.fshHost.focus();
        }),
        ...foundList("IRD Ateme", r.ird,
          (i) => [`${i.name || "Sans nom"} (${i.model}) — ${i.host}`, `${i.input} · n° ${i.serial}`, known.has(i.serial) ? "" : "Ajouter"],
          (i) => act(() => Store.post("/api/irds", { host: i.host }), err).then(() => scan(btn, info, found, err))),
      );
    } catch (e) {
      showError(err, `Recherche impossible : ${e.message}`);
      info.textContent = "";
    } finally {
      btn.disabled = false;
    }
  }

  function irdsCard(irds) {
    const err = errorBox();
    const body = el("tbody", {}, ...irds.map((ird) => irdRow(ird, err)));
    if (!irds.length) body.append(el("tr", {}, el("td", { colSpan: 6, className: "muted", textContent: "Aucun IRD suivi." })));
    const table = el("table", { className: "table" },
      el("thead", {}, el("tr", {}, ...["Nom", "Adresse", "Polarisation", "Porteuse", "État", ""].map((h) => el("th", { textContent: h })))), body);
    const host = el("input", { className: "input", placeholder: "Adresse d'un IRD à ajouter", spellcheck: false });
    const add = el("form", { className: "form-row" }, host, el("button", { type: "submit", className: "btn", textContent: "Ajouter" }));
    add.addEventListener("submit", (e) => {
      e.preventDefault();
      const value = host.value.trim();
      if (!value) return;
      act(() => Store.post("/api/irds", { host: value }), err);
    });
    const info = el("p", { className: "muted note" });
    const found = el("div", { className: "found-zone" });
    const scanBtn = button("Rechercher sur le réseau", () => scan(scanBtn, info, found, err));
    add.append(scanBtn);
    return cardBox("IRD suivis", table, add, err, info, found);
  }

  async function renderDevices(mine) {
    const d = await Store.getJSON("/api/devices");
    if (mine !== seq || !dom) return;
    // La recherche sur le réseau et ses résultats survivent aux rafraîchissements.
    if (!dom.irdsCard) {
      dom.fshCard = fshCard(d.fsh);
      dom.irdsCard = irdsCard(d.irds);
      dom.body.replaceChildren(dom.fshCard, dom.irdsCard);
      return;
    }
    const fsh = fshCard(d.fsh);
    dom.fshCard.replaceWith(fsh);
    dom.fshCard = fsh;
    const err = dom.irdsCard.querySelector(".error");
    const body = el("tbody", {}, ...d.irds.map((ird) => irdRow(ird, err)));
    if (!d.irds.length) body.append(el("tr", {}, el("td", { colSpan: 6, className: "muted", textContent: "Aucun IRD suivi." })));
    dom.irdsCard.querySelector("tbody").replaceWith(body);
  }

  // ----- Porteuses -----

  async function renderCarriers(mine) {
    const carriers = await Store.getJSON("/api/carriers");
    if (mine !== seq || !dom) return;
    dom.carrierErr ??= errorBox();
    const err = dom.carrierErr;
    const rows = carriers.map((c) => {
      const name = el("input", { className: "input", value: c.name });
      name.addEventListener("change", () => act(() => Store.post(`/api/carriers/${enc(c.key)}`, { name: name.value }), err));
      const remove = c.manual ? button("Supprimer", () => act(() => Store.post(`/api/carriers/${enc(c.key)}/remove`), err), { small: true }) : "";
      return el("tr", {}, el("td", {}, name), el("td", { className: "num", textContent: `${fmtMhz(c.rf_khz)} MHz` }),
        el("td", { textContent: POL_NAME[c.polarization] || "inconnue" }),
        el("td", { className: "num", textContent: c.symbol_rate ? `${num(c.symbol_rate / 1e6, 2)} MS/s` : "—" }),
        el("td", { className: "num", textContent: String(c.irds.length) }), el("td", { textContent: c.manual ? "saisie" : "IRD" }), el("td", {}, remove));
    });
    if (!rows.length) rows.push(el("tr", {}, el("td", { colSpan: 7, className: "muted", textContent: "Aucune porteuse : ajoute un IRD ou une porteuse." })));
    const table = el("table", { className: "table" },
      el("thead", {}, el("tr", {}, ...["Nom", "Fréquence", "Polarisation", "Débit symbole", "IRD", "Origine", ""].map((h) => el("th", { textContent: h })))),
      el("tbody", {}, ...rows));
    if (!dom.addCard) dom.addCard = carrierAddCard();
    dom.body.replaceChildren(cardBox("Porteuses", el("p", { className: "muted note",
      textContent: "Regroupées automatiquement à partir du réglage des IRD ; une porteuse saisie à la main peut être supprimée." }), table, err), dom.addCard);
  }

  function carrierAddCard() {
    const name = el("input", { className: "input", required: true });
    const freq = el("input", { className: "input", type: "number", step: "0.001", min: "0", required: true });
    const pol = select([["", "inconnue"], ...POL_OPTIONS], "");
    const sr = el("input", { className: "input", type: "number", step: "0.001", min: "0" });
    const err = errorBox();
    const form = el("form", { className: "form-row" }, field("Nom", name), field("Fréquence (MHz)", freq), field("Polarisation", pol),
      field("Débit symbole (MS/s)", sr), el("button", { type: "submit", className: "btn primary", textContent: "Ajouter" }));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      act(async () => {
        await Store.post("/api/carriers", { name: name.value.trim(), freq_mhz: Number(freq.value), polarization: pol.value || null,
          symbol_rate_msps: sr.value ? Number(sr.value) : null });
        form.reset();
      }, err);
    });
    return cardBox("Ajouter une porteuse", form, err);
  }

  // ----- Seuils -----

  // Valeurs lues sur le service au moment d'afficher la page (jamais les valeurs par défaut de la page).
  async function renderThresholds(mine) {
    const cfg = (await Store.getJSON("/api/status")).config;
    if (mine !== seq || !dom) return;
    const drop = el("input", { className: "input", type: "number", min: "0.5", max: "20", step: "0.1", required: true, value: cfg.margin_drop_db ?? 2 });
    const min = el("input", { className: "input", type: "number", min: "0", max: "30", step: "0.1", required: true, value: cfg.margin_min_db ?? 0 });
    const err = errorBox();
    const ok = el("p", { className: "ok-note", hidden: true, textContent: "Seuils enregistrés." });
    const form = el("form", { className: "thresholds" },
      field("Chute rapide (dB)", drop, "« Chute de marge » si la marge perd autant en quelques secondes par rapport à la minute précédente. Ligne orange sur les graphes."),
      field("Marge minimale (dB)", min, "« Marge basse » si la marge reste 3 s sous cette valeur ; 0 = désactivée. Ligne rouge sur les graphes."),
      el("div", {}, el("button", { type: "submit", className: "btn primary", textContent: "Enregistrer" })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      showError(err, "");
      ok.hidden = true;
      try {
        await Store.post("/api/detect", { margin_drop_db: Number(drop.value), margin_min_db: Number(min.value) });
        ok.hidden = false;
      } catch (e2) {
        showError(err, e2.message);
      }
      Store.reload();
    });
    dom.body.replaceChildren(cardBox("Seuils d'alerte sur la marge",
      el("p", { className: "muted note", textContent: "La marge est la réserve avant décrochage : à 0 dB, l'IRD perd le lock. Les deux seuils s'appliquent à tous les IRD, tout de suite, et sont enregistrés dans config.toml." }),
      form, err, ok));
  }

  async function refresh() {
    if (!dom) return;
    const mine = ++seq;
    try {
      if (route.tab === "appareils") await renderDevices(mine);
      else if (route.tab === "porteuses") await renderCarriers(mine);
      else await renderThresholds(mine);
    } catch (e) {
      if (mine === seq && dom) dom.body.replaceChildren(el("p", { className: "error", textContent: `Chargement impossible : ${e.message}` }));
    }
  }

  // États des appareils : la page Appareils suit les changements (sans toucher aux champs en cours de saisie).
  function onStore(kind) {
    if (route.tab !== "appareils" || kind !== "state") return;
    if (dom.body.contains(document.activeElement) && document.activeElement.matches("input, select")) return;
    refresh();
  }

  function mount(view, r) {
    route = r;
    dom = { body: el("div", { className: "settings" }, emptyState("Chargement…")) };
    view.append(el("div", { className: "page" }, el("div", { className: "crumb", textContent: "Réglages" }),
      ...pageHead(TITLES[route.tab]), dom.body));
    refresh();
    unsubscribe = Store.subscribe(onStore);
  }

  function unmount() {
    seq++;
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    dom = route = null;
  }

  return { mount, unmount };
})();
