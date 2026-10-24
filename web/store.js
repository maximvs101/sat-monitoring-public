"use strict";

// Données partagées par toutes les pages : statut du service, fenêtre du direct (séries, dernières mesures,
// événements), flux en direct. Les pages s'abonnent (`subscribe`) et chargent une période passée avec `loadRange`.

const Store = (() => {
  const RECONNECT_MS = 5000;
  const state = {
    cfg: { live_window_min: 10, event_padding_s: 30, max_points: 5000 },
    connected: false,       // flux en direct ouvert
    carriers: [],           // porteuses actuelles (avec marqueurs, IRD à polarisation inconnue)
    followed: [],           // numéros de série des IRD suivis, dans l'ordre de /api/status
    names: {},              // numéro de série → nom
    hosts: {},              // numéro de série → adresse
    states: {},             // « fsh » / « ird:<série> » → état de l'appareil (up, down, paused, unknown)
    details: {},            // « fsh » / « ird:<série> » → motif d'un état « down »
    seen: {},               // « fsh » / « ird:<série> » → heure de la dernière mesure
    fshInfo: {},            // FSH4 : adresse, port, polarisation, span, RBW
    latest: {},             // numéro de série → dernière mesure complète (décodage, état matériel, détails)
    irds: {},               // numéro de série → séries de la fenêtre du direct { ts, margin_db, locked, state, power_dbm }
    fsh: { ts: [], nums: [], levels: {}, freqs: {} },  // marqueurs FSH4 de la fenêtre du direct
    noise: [],              // marqueurs placés sur le plancher de bruit
    ifWarning: false,       // marqueurs en fréquence intermédiaire (décalage LNB non réglé sur le FSH4)
    events: new Map(),      // id → événement (fenêtre du direct, et tous ceux en cours)
  };
  const subscribers = new Set();
  let pending = null;  // mesures reçues pendant un chargement, rejouées ensuite
  let loadSeq = 0;
  let source = null;

  function notify(kind) {
    for (const fn of [...subscribers]) {
      try { fn(kind); } catch (e) { console.error(e); }
    }
  }

  // fn(kind) : « event », « state », « tick » (chaque seconde, mesures reçues comprises), « reload ».
  function subscribe(fn) {
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  }

  async function getJSON(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`${url} : HTTP ${res.status}`);
    return res.status === 204 ? null : res.json();
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {}; }
    if (!res.ok) throw new Error(data.erreur || `Erreur HTTP ${res.status}`);
    return data;
  }

  function liveRange() {
    const now = Date.now();
    return [now - state.cfg.live_window_min * 60_000, now];
  }

  function applyStatus(st) {
    state.cfg = st.config;
    state.carriers = st.carriers;
    state.noise = st.marker_map.noise;
    state.ifWarning = st.marker_map.if_warning;
    state.followed = Object.keys(st.irds);
    state.fshInfo = st.fsh;
    state.states.fsh = st.fsh.state;
    state.details.fsh = st.devices.fsh?.detail || "";
    if (st.marker) state.seen.fsh = st.marker.ts;
    for (const [serial, ird] of Object.entries(st.irds)) {
      state.names[serial] = ird.name;
      state.hosts[serial] = ird.host;
      state.states[`ird:${serial}`] = ird.state;
      state.details[`ird:${serial}`] = st.devices[`ird:${serial}`]?.detail || "";
      if (ird.latest) {
        state.latest[serial] = ird.latest;
        state.seen[`ird:${serial}`] = ird.latest.ts;
      }
    }
  }

  // Séries de /api/series : marqueurs alignés sur un même axe de temps, séries par IRD telles quelles.
  function convertSeries(data) {
    const nums = Object.keys(data.fsh).map(Number).sort((a, b) => a - b);
    const ts = [...new Set(nums.flatMap((n) => data.fsh[n].ts))].sort((a, b) => a - b);
    const index = new Map(ts.map((t, i) => [t, i]));
    const levels = {}, freqs = {}, levelsMin = {}, levelsMax = {};
    for (const n of nums) {
      const s = data.fsh[n];
      levels[n] = new Array(ts.length).fill(null);
      levelsMin[n] = new Array(ts.length).fill(null);  // agrégée : la plus basse et la plus haute de chaque intervalle
      levelsMax[n] = new Array(ts.length).fill(null);
      s.ts.forEach((t, i) => {
        const k = index.get(t);
        levels[n][k] = s.level_dbm[i];
        levelsMin[n][k] = (s.level_dbm_min || s.level_dbm)[i];
        levelsMax[n][k] = (s.level_dbm_max || s.level_dbm)[i];
      });
      freqs[n] = s.freq_hz.length ? s.freq_hz[s.freq_hz.length - 1] : null;
    }
    const irds = {};
    for (const [serial, s] of Object.entries(data.irds)) {
      irds[serial] = { ts: s.ts, margin_db: s.margin_db, locked: s.locked, state: s.state, power_dbm: s.power_dbm,
        margin_min: s.margin_db_min || s.margin_db,  // agrégée : la plus basse de chaque intervalle
        state_ms: s.state_ms || null };              // agrégée : temps passé dans chaque état, par intervalle
    }
    const aggregated = Object.values(data.fsh).some((s) => s.aggregated) || Object.values(data.irds).some((s) => s.aggregated);
    return { fsh: { ts, nums, levels, levelsMin, levelsMax, freqs }, irds, aggregated };
  }

  // Période passée : séries (agrégées selon `points`), porteuses de la période, événements. Ne touche pas au direct.
  async function loadRange(from, to, points = 1500) {
    const [series, events] = await Promise.all([
      getJSON(`/api/series?from=${from}&to=${to}&max_points=${Math.min(points, state.cfg.max_points)}`),
      getJSON(`/api/events?from=${from}&to=${to}`),
    ]);
    Object.assign(state.names, series.names);
    return { ...convertSeries(series), carriers: series.carriers, noise: series.noise, ifWarning: series.if_warning,
      names: series.names, events, from, to };
  }

  // Fenêtre du direct : statut, séries brutes, événements ; les mesures reçues entre-temps sont rejouées.
  async function loadLive() {
    const seq = ++loadSeq;
    pending = pending || [];
    let status, series, events;
    try {
      status = await getJSON("/api/status");
      const [from, to] = [status.now - status.config.live_window_min * 60_000, status.now];
      [series, events] = await Promise.all([
        // Assez de points pour toute la fenêtre (FSH4 jusqu'à 4 Hz) : sinon le service regrouperait les points et
        // les mesures reçues ensuite s'ajouteraient brutes à une série regroupée.
        getJSON(`/api/series?from=${from}&to=${to}&max_points=${Math.max(status.config.max_points, status.config.live_window_min * 60 * 5)}`),
        getJSON(`/api/events?from=${from}&to=${to}`),
      ]);
    } finally {
      if (seq !== loadSeq) return;  // dépassé par un chargement plus récent
      const buffered = pending || [];
      pending = null;
      if (series) {
        applyStatus(status);
        const converted = convertSeries(series);
        state.fsh = converted.fsh;
        state.irds = converted.irds;
        Object.assign(state.names, series.names);
        state.events = new Map(events.map((e) => [e.id, e]));
        // Reçu pendant le chargement : plus récent que l'instantané (alerte ouverte ou fermée entre-temps).
        for (const msg of buffered) {
          if (msg.type === "event") state.events.set(msg.id, msg);
          else appendLive(msg);
        }
      }
    }
    notify("reload");
  }

  // Un rechargement demandé pendant un autre est fait juste après (une porteuse apparue entre-temps…).
  let reloading = false, again = false;
  function reload() {
    if (reloading) {
      again = true;
      return;
    }
    reloading = true;
    loadLive().catch((e) => console.error(e)).finally(() => {
      reloading = false;
      if (again) {
        again = false;
        reload();
      }
    });
  }

  function trimLive() {
    const [from] = liveRange();
    const cut = (ts, arrays) => {
      let n = 0;
      while (n < ts.length && ts[n] < from) n++;
      if (n) for (const a of [ts, ...arrays]) a.splice(0, n);
    };
    cut(state.fsh.ts, state.fsh.nums.map((k) => state.fsh.levels[k]));
    for (const d of Object.values(state.irds)) cut(d.ts, [d.margin_db, d.locked, d.state, d.power_dbm]);
    for (const [id, ev] of state.events) {
      if (ev.ts_end != null && ev.ts_end < from) state.events.delete(id);
    }
  }

  function appendLive(msg) {
    const last = (arr) => (arr.length ? arr[arr.length - 1] : -Infinity);
    if (msg.type === "markers" && msg.ts > last(state.fsh.ts)) {
      const f = state.fsh;
      const nums = Object.keys(msg.markers).map(Number).sort((a, b) => a - b);
      const changed = nums.join() !== f.nums.join() || nums.some((n) => msg.markers[n].freq_hz !== f.freqs[n]);
      if (changed && !pending) {
        reload();  // marqueur allumé, éteint ou déplacé : tout recharger
        return;
      }
      f.ts.push(msg.ts);
      for (const n of f.nums) f.levels[n].push(msg.markers[n]?.level_dbm ?? null);
    } else if (msg.type === "ird") {
      const d = state.irds[msg.serial];
      if (!d) {
        if (!pending) reload();  // IRD nouveau pour la page
        return;
      }
      if (msg.ts > last(d.ts)) {
        d.ts.push(msg.ts); d.margin_db.push(msg.margin_db); d.locked.push(msg.locked ? 1 : 0);
        d.state.push(msg.state); d.power_dbm.push(msg.power_dbm);  // state : code de la bande, calculé par le service
      }
    }
  }

  function onLive(msg) {
    if (msg.type === "ird" && msg.ts > (state.latest[msg.serial]?.ts ?? -Infinity)) {
      // L'état matériel et les détails n'arrivent que toutes les 10 s : on garde les précédents entre deux relevés.
      const prev = state.latest[msg.serial];
      state.latest[msg.serial] = { ...msg, health: msg.health || prev?.health || null, details: msg.details || prev?.details || null };
    }
    if (msg.type === "markers" || msg.type === "ird") {
      state.seen[msg.type === "markers" ? "fsh" : `ird:${msg.serial}`] = msg.ts;
      if (pending) pending.push(msg);
      else appendLive(msg);  // affiché au prochain « tick » : au plus un rendu par seconde, quel que soit le débit
    } else if (msg.type === "state") {
      state.states[msg.device] = msg.state;
      state.details[msg.device] = msg.detail || "";
      notify("state");
    } else if (msg.type === "carriers") {
      reload();
    } else if (msg.type === "event") {
      state.events.set(msg.id, msg);
      if (pending) pending.push(msg);
      notify("event");
    }
  }

  // `again` : nouvelle connexion après un abandon du navigateur ; à l'ouverture, tout est rechargé.
  function startLive(again = false) {
    source = new EventSource("/api/live");
    source.onmessage = (e) => onLive(JSON.parse(e.data));
    let opened = again;
    source.onopen = () => {
      state.connected = true;
      notify("state");
      // Après une coupure (service redémarré…), tout recharger : des événements ont pu être fermés entre-temps.
      if (opened) reload();
      opened = true;
    };
    source.onerror = () => {
      state.connected = false;
      notify("state");
      // Sur une réponse HTTP en erreur (service en cours de redémarrage…), le navigateur abandonne pour de bon :
      // on rouvre nous-mêmes un peu plus tard.
      if (source.readyState === EventSource.CLOSED) setTimeout(() => startLive(true), RECONNECT_MS);
    };
  }

  // La minuterie démarre avant le premier chargement : s'il échoue, la fenêtre du direct reste bornée.
  async function start() {
    startLive();
    setInterval(() => {
      trimLive();
      notify("tick");  // l'axe du temps avance même sans nouvelle mesure
    }, 1000);
    await loadLive();
  }

  // Événements en cours ou de la fenêtre du direct, les plus récents d'abord.
  function liveEvents() {
    return [...state.events.values()].sort((a, b) => (b.ts_end == null) - (a.ts_end == null) || b.ts_start - a.ts_start || b.id - a.id);
  }

  // Événements en cours d'un IRD (« ird:<série> ») ou d'une porteuse.
  function openEvents(predicate = () => true) {
    return [...state.events.values()].filter((e) => e.ts_end == null && predicate(e));
  }

  return { state, subscribe, start, reload, loadRange, liveRange, liveEvents, openEvents, getJSON, post, applyStatus };
})();
