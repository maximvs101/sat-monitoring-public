"use strict";

// Son des alertes dans la page : alarme répétée pour une alerte critique tant qu'elle n'est ni acquittée ni fermée,
// un bip pour un avertissement, bandeau d'acquittement en haut de la page, notification du navigateur en option.
// Réglages propres à chaque navigateur (localStorage). Les alertes déjà en cours au chargement de la page ne
// sonnent pas : seules celles qui s'ouvrent ensuite.

const Alerts = (() => {
  const KEY = "monitoring-sat.alertes";
  const DEFAULTS = { sound: true, critical: true, warning: true, repeat_s: 30, volume: 0.6, browser: false };
  let prefs = load();
  let audio = null;          // AudioContext, créé au premier son (les navigateurs exigent un clic avant de jouer)
  let known = null;          // événements déjà vus (ceux du chargement ne sonnent pas)
  const ringing = new Map(); // id → { ev, next } : alarmes critiques non acquittées
  let banner = null;
  const rows = new Map();     // id → ligne du bandeau
  let allRow = null;
  let soundButton = null;

  function load() {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
    } catch {
      return { ...DEFAULTS };
    }
  }

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(prefs));
    } catch {
      // navigation privée, stockage bloqué : les réglages valent pour la session
    }
  }

  function set(changes) {
    prefs = { ...prefs, ...changes };
    save();
    if (!prefs.critical) ringing.clear();  // son coupé : le bandeau reste (avertissement visuel), seul le son se tait
    render();
  }

  function context() {
    if (!audio) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      audio = new AC();
    }
    return audio;
  }

  // Le son n'est possible qu'après un clic dans la page (règle des navigateurs).
  function locked() {
    const c = context();
    return !c || c.state !== "running";
  }

  function unlock() {
    const c = context();
    if (c) c.resume().then(render).catch(() => {});
  }

  // Notes : [fréquence en Hz, début en s, durée en s].
  function tones(notes) {
    const c = context();
    if (!c || c.state !== "running") return false;
    const gain = c.createGain();
    gain.gain.value = prefs.volume;
    gain.connect(c.destination);
    const t0 = c.currentTime + 0.02;
    for (const [freq, start, dur] of notes) {
      const osc = c.createOscillator();
      osc.type = "square";
      osc.frequency.value = freq;
      const env = c.createGain();
      env.gain.setValueAtTime(0, t0 + start);
      env.gain.linearRampToValueAtTime(0.25, t0 + start + 0.01);
      env.gain.setValueAtTime(0.25, t0 + start + dur - 0.02);
      env.gain.linearRampToValueAtTime(0, t0 + start + dur);
      osc.connect(env).connect(gain);
      osc.start(t0 + start);
      osc.stop(t0 + start + dur);
    }
    return true;
  }

  // Alarme : trois fois deux tons (aigu, grave) ; bip : un ton bref.
  function play(kind) {
    if (!prefs.sound) return false;
    if (kind === "alarm") return tones([0, 0.5, 1.0].flatMap((t) => [[880, t, 0.22], [660, t + 0.25, 0.22]]));
    return tones([[990, 0, 0.18]]);
  }

  function notifyBrowser(ev) {
    if (!prefs.browser || !("Notification" in window) || Notification.permission !== "granted") return;
    const n = new Notification(`${ev.severity === "critical" ? "Alerte critique" : "Avertissement"} : ${KINDS[ev.kind] || ev.kind}`,
      { body: `${eventSource(ev)} — ${ev.detail}`, tag: `evenement-${ev.id}` });
    n.onclick = () => {
      window.focus();
      const target = eventTarget(ev, Store.state.carriers, Date.now(), Store.state.cfg.event_padding_s * 1000);
      if (target) location.hash = routeHref(target);
      n.close();
    };
  }

  function acknowledge(id) {
    if (id == null) ringing.clear();
    else ringing.delete(id);
    render();
  }

  function onStore(kind) {
    if (known === null) return;
    const now = Date.now();
    let alarm = false, beep = false;
    for (const ev of Store.state.events.values()) {
      if (known.has(ev.id)) continue;
      known.add(ev.id);
      const sound = alertSound(ev, prefs);
      if (sound === "alarm") {
        alarm = true;
        ringing.set(ev.id, { ev, next: now + prefs.repeat_s * 1000 });
      } else if (sound === "beep") {
        beep = true;
      }
      if (ev.ts_end == null && (ev.severity === "critical" || ev.severity === "warning")) notifyBrowser(ev);
    }
    // Fermée (ou oubliée) : l'alarme s'arrête d'elle-même.
    for (const [id, r] of ringing) {
      const ev = Store.state.events.get(id);
      if (!ev || ev.ts_end != null) ringing.delete(id);
      else r.ev = ev;
    }
    // Répétition des alarmes non acquittées : un seul son par seconde, quel que soit leur nombre.
    if (kind === "tick" && prefs.repeat_s > 0) {
      for (const r of ringing.values()) {
        if (now >= r.next) {
          alarm = true;
          r.next = now + prefs.repeat_s * 1000;
        }
      }
    }
    if (alarm) play("alarm");
    else if (beep) play("beep");
    render();
  }

  function render() {
    if (soundButton) {
      const off = !prefs.sound;
      const needsClick = prefs.sound && locked();
      soundButton.textContent = off ? "🔕 Son coupé" : needsClick ? "🔈 Activer le son" : "🔔 Son activé";
      soundButton.className = `nav-sound${off ? " off" : needsClick ? " locked" : ""}`;
      soundButton.title = off ? "Les alertes ne sonnent pas dans cette page (Réglages › Notifications)"
        : needsClick ? "Le navigateur attend un clic avant de pouvoir jouer un son" : "Cliquer pour couper le son";
    }
    if (!banner) return;
    banner.hidden = !ringing.size;
    // Une ligne par alarme, créée une fois puis mise à jour sur place : un bouton « Acquitter » ne disparaît jamais
    // sous le curseur parce qu'une autre alarme vient d'arriver.
    for (const [id, row] of rows) {
      if (!ringing.has(id)) { row.el.remove(); rows.delete(id); }
    }
    const order = [...ringing.values()].sort((a, b) => b.ev.ts_start - a.ev.ts_start);
    order.forEach(({ ev }, i) => {
      let row = rows.get(ev.id);
      if (!row) {
        row = { text: el("span") };
        row.el = el("div", { className: "alarm-row" }, el("span", { className: "dot bad" }),
          el("strong", { textContent: KINDS[ev.kind] || ev.kind }), row.text,
          button("Acquitter", () => acknowledge(ev.id), { small: true }));
        rows.set(ev.id, row);
      }
      row.text.textContent = ` · ${eventSource(ev)} · ${fmtTime(ev.ts_start)} — ${ev.detail}`;
      if (banner.children[i] !== row.el) banner.insertBefore(row.el, banner.children[i] || null);
    });
    if (!allRow) allRow = el("div", { className: "alarm-row" }, button("Tout acquitter", () => acknowledge(null), { small: true }));
    if (ringing.size > 1) banner.append(allRow);
    else allRow.remove();
  }

  // Bouton du menu : couper / remettre le son, ou débloquer le son (premier clic).
  function navButton() {
    if (!soundButton) {
      soundButton = el("button", { type: "button" });
      soundButton.addEventListener("click", () => {
        if (prefs.sound && locked()) unlock();
        else set({ sound: !prefs.sound });
        if (prefs.sound) unlock();
      });
      render();
    }
    return soundButton;
  }

  function init(bannerEl) {
    banner = bannerEl;
    known = new Set(Store.state.events.keys());
    // Le premier clic n'importe où dans la page débloque le son.
    document.addEventListener("pointerdown", unlock, { once: true });
    document.addEventListener("keydown", unlock, { once: true });
    Store.subscribe(onStore);
    render();
  }

  return { init, navButton, prefs: () => ({ ...prefs }), set, play, unlock, locked, acknowledge };
})();
