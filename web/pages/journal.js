"use strict";

// Journal : tous les événements du parc sur une période, les plus récents en haut ; filtres période, source (porteuse,
// IRD, FSH4), « en cours seulement », tous dans l'adresse. Un clic ouvre la porteuse où se trouvait l'IRD au moment
// de l'événement (ou l'analyseur FSH4), calée sur l'événement.

const JournalPage = (() => {
  const CHOICES = [["24h", "24 h"], ["7j", "7 j"], ["30j", "30 j"]];
  const MAX_ROWS = 500;  // au-delà, seuls les plus récents sont affichés (la page reste fluide)
  let route = null;
  let dom = null;
  let unsubscribe = null;
  let seq = 0;
  let events = [];
  let periodCarriers = [];  // porteuses de la période, avec les périodes d'appartenance de chaque IRD
  let refresh = null;

  const period = () => route.period || "24h";

  function go(changes) {
    location.hash = routeHref({ page: "journal", period: route.period, src: route.src, open: route.open, ...changes });
  }

  // Une porteuse filtre ses propres événements et ceux des IRD qui étaient sur elle au moment de l'événement.
  function matches(ev) {
    if (route.open && ev.ts_end != null) return false;
    const src = route.src;
    if (!src) return true;
    if (src.startsWith("porteuse:")) {
      if (ev.source === src) return true;
      const key = src.slice(9);
      const c = periodCarriers.find((x) => x.key === key) || Store.state.carriers.find((x) => x.key === key);
      if (!c || !ev.source?.startsWith("ird:")) return false;
      const serial = ev.source.slice(4);
      return c.irds.includes(serial) && Charts.inPeriods(c, serial, ev.ts_start);
    }
    return ev.source === src;
  }

  function sourceOptions() {
    const s = Store.state;
    const opts = [["", "Toutes les sources"]];
    for (const c of s.carriers) opts.push([`porteuse:${c.key}`, `Porteuse ${c.name}`]);
    // Porteuses de la période qui n'existent plus aujourd'hui (ancien site, IRD re-réglé).
    for (const c of periodCarriers) {
      if (!s.carriers.some((x) => x.key === c.key)) opts.push([`porteuse:${c.key}`, `Porteuse ${c.name} (plus aujourd'hui)`]);
    }
    const serials = new Set([...s.followed, ...events.filter((e) => e.source?.startsWith("ird:")).map((e) => e.source.slice(4))]);
    for (const serial of [...serials].sort((a, b) => (s.names[a] || a).localeCompare(s.names[b] || b, "fr", { numeric: true }))) {
      opts.push([`ird:${serial}`, s.names[serial] || serial]);
    }
    opts.push(["fsh", "FSH4"]);
    if (route.src && !opts.some(([v]) => v === route.src)) opts.push([route.src, route.src]);
    return opts;
  }

  async function onEventClick(ev) {
    const page = dom;  // page quittée pendant le chargement : pas de navigation
    const { target, message } = await eventDestination(ev);
    if (dom !== page) return;
    if (target) location.hash = routeHref(target);
    else showNotice(message);
  }

  function showNotice(text) {
    dom.error.hidden = false;
    dom.error.textContent = text;
  }

  function render() {
    const matching = events.filter(matches);
    const shown = matching.slice(0, MAX_ROWS);
    dom.count.textContent = matching.length > MAX_ROWS ? `${MAX_ROWS} plus récents affichés sur ${matching.length} événements`
      : `${matching.length} événement${matching.length > 1 ? "s" : ""}`;
    const options = sourceOptions();
    if (dom.source.dataset.sig !== JSON.stringify(options)) {
      dom.source.dataset.sig = JSON.stringify(options);
      dom.source.replaceChildren(...options.map(([value, label]) => el("option", { value, textContent: label })));
    }
    dom.source.value = route.src || "";
    dom.list.replaceChildren(shown.length ? eventList(shown, { onClick: onEventClick })
      : emptyState(route.open ? "Aucun événement en cours." : "Aucun événement sur la période."));
  }

  async function load() {
    const mine = ++seq;
    const [from, to] = periodRange(period(), Date.now(), Store.state.cfg.live_window_min);
    try {
      const list = await Store.getJSON(`/api/events?from=${from}&to=${to}`);
      if (mine !== seq || !dom) return;
      // Les événements encore en cours sont toujours listés, même ouverts avant la période.
      const byId = new Map(list.map((e) => [e.id, e]));
      for (const e of Store.openEvents()) if (!byId.has(e.id)) byId.set(e.id, e);
      events = [...byId.values()].sort((a, b) => b.ts_start - a.ts_start);
      dom.error.hidden = true;
      render();
      // Porteuses de la période (qui était où), pour le filtre : chargées ensuite, la liste s'affiche sans attendre.
      Store.loadRange(from, to, 50).then((past) => {
        if (mine !== seq || !dom) return;
        periodCarriers = past.carriers;
        render();
      }).catch(() => {});
    } catch (e) {
      if (mine !== seq || !dom) return;
      dom.error.hidden = false;
      dom.error.textContent = `Chargement impossible : ${e.message}`;
    }
  }

  // Périodes glissantes : nouvel événement ou fin d'un événement → liste rechargée (au plus une fois par seconde).
  function onStore(kind) {
    if (kind !== "event" && kind !== "reload") return;
    if (!isFixedPeriod(period())) return;
    clearTimeout(refresh);
    refresh = setTimeout(load, 1000);
  }

  function mount(view, r) {
    route = r;
    dom = {
      count: el("span", { className: "meta" }),
      source: el("select", { className: "input" }),
      list: el("div"),
      error: el("p", { className: "error", hidden: true }),
    };
    dom.source.addEventListener("change", () => go({ src: dom.source.value || undefined }));
    const open = el("input", { type: "checkbox", checked: !!route.open });
    open.addEventListener("change", () => go({ open: open.checked ? "1" : undefined }));
    const filters = el("div", { className: "filters" },
      PeriodSelector.build(period(), (p) => go({ period: p === "24h" ? undefined : p }), { choices: CHOICES }),
      dom.source, el("label", { className: "check" }, open, " En cours seulement"));
    view.append(el("div", { className: "page" }, ...pageHead("Journal des événements", { actions: [dom.count] }),
      filters, dom.error, cardBox(null, dom.list)));
    dom.list.append(emptyState("Chargement…"));
    load();
    unsubscribe = Store.subscribe(onStore);
  }

  function unmount() {
    seq++;
    clearTimeout(refresh);
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    dom = route = null;
    events = [];
  }

  return { mount, unmount };
})();
