"""Application : collecte FSH4 + IRD multiples, porteuses, détection et diffusion du direct."""

import logging
import queue
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path

from . import discover, system
from .carriers import Carrier, Tuning, attach_markers, center_marker, compatible_pol, entry_matches, group_carriers
from .collector import DeviceLoop
from .config import CarrierEntry, Config, IrdEntry, save_carriers, save_detect, save_fsh, save_irds
from .detect import Detector
from .fsh import FshClient, FshReader
from .ird import IrdClient, IrdReader, IrdReading, band_state
from .store import Store

log = logging.getLogger("monitoring")

QUEUE_SIZE = 1000
UNCHANGED = object()  # « paramètre non fourni », distinct de None (= polarisation automatique)
PURGE_EVERY_S = 3600


class LiveBus:
    """Diffuse les messages du direct ; un abonné lent perd ses plus anciens messages."""

    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(QUEUE_SIZE)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            while True:
                try:
                    q.put_nowait(msg)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass


def _hz(value: float) -> str:
    for unit, scale in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if value >= scale:
            return f"{value / scale:.1f}".removesuffix(".0").replace(".", ",") + f" {unit}"
    return f"{value:.0f} Hz"


def _mhz(freq_hz: float) -> str:
    text = f"{freq_hz / 1e6:,.1f}".removesuffix(".0")
    return text.replace(",", " ").replace(".", ",") + " MHz"


def describe_settings(settings) -> str:
    markers = ", ".join(f"M{n} {_mhz(freq)}" for n, freq in settings.markers) or "aucun marqueur allumé"
    return f"{markers}, span {_hz(settings.span_hz)}, RBW {_hz(settings.rbw_hz)}"


def default_fsh(cfg: Config):
    client = FshClient(cfg.fsh)
    return FshReader(client).read, client.close


def default_ird_factory(entry: IrdEntry, cfg: Config):
    """(lecture, identification) d'un IRD ; la lecture vérifie son numéro de série."""
    client = IrdClient(entry.host, cfg.ird)
    return IrdReader(client, entry.serial).read, client.identify


def _now_ms() -> int:
    return int(time.time() * 1000)


def carrier_dict(c: Carrier) -> dict:
    d = asdict(c)
    d["irds"] = list(c.irds)
    return d


def carrier_view(c: Carrier, mm, freqs: dict) -> dict:
    """Porteuse telle que la page la reçoit : avec ses marqueurs FSH4 et son marqueur central."""
    return {**carrier_dict(c), "markers": list(mm.by_carrier.get(c.key, ())), "center_marker": center_marker(c, mm, freqs)}


class App:
    def __init__(self, cfg: Config, fsh_read=None, fsh_close=None, ird_factory=None, web_dir: Path | None = None,
                 config_path: Path | None = None, scanner=None):
        self.cfg = cfg
        self.config_path = config_path
        self.ird_factory = ird_factory or default_ird_factory
        self.scanner = scanner or discover.scan
        self.web_dir = (web_dir or system.web_dir()).resolve()
        self.store = Store(cfg.paths.db)
        self.detector = Detector(self.store, cfg.detect)
        self.bus = LiveBus()
        self._detect_lock = threading.RLock()
        self._devices_lock = threading.RLock()
        self._started = False
        self._stopping = threading.Event()
        self._purger = threading.Thread(target=self._purge_loop, name="purge", daemon=True)
        self._identifier = threading.Thread(target=self._identify_loop, name="identification", daemon=True)

        if fsh_read is None:
            fsh_read, fsh_close = default_fsh(cfg)
        d = cfg.detect
        self.fsh_loop = DeviceLoop("fsh", 1 / cfg.fsh.rate_hz, fsh_read, self._on_marker,
                                   lambda ts, st, detail: self._on_state(ts, "fsh", st, detail),
                                   d.down_after_failures, d.retry_s, close=fsh_close,
                                   min_idle_s=cfg.fsh.min_idle_s)
        self._fsh_key = None
        self.marker_freqs = {}
        self.marker_map = attach_markers([], {}, cfg.fsh.polarization)

        self.names = {}       # numéro de série → nom
        self.hosts = {}       # numéro de série → adresse
        self.tunings = {}     # numéro de série → dernier Tuning (polarisation effective)
        self.pols = {}        # numéro de série → polarisation imposée par l'opérateur (None = automatique)
        self.read_pols = {}   # numéro de série → polarisation lue sur l'IRD (s'il alimente le LNB)
        self.details = {}     # numéro de série → dernier IrdDetails (affiché, pas enregistré)
        self.ird_loops = {}   # numéro de série → DeviceLoop
        self.pending = []     # IRD de config.toml pas encore identifiés (ancien format)
        self._carriers_sig = None
        self._carriers_lock = threading.RLock()  # calcul et publication des porteuses (plusieurs fils d'IRD)
        self._last_carriers = []  # pour garder les clés de porteuse stables d'un calcul à l'autre
        for entry in cfg.irds:
            if entry.serial:
                self._register(entry)
            else:
                self.pending.append(entry)

    # --- IRD ---

    def _register(self, entry: IrdEntry) -> None:
        serial = entry.serial
        self.names[serial] = entry.name
        self.hosts[serial] = entry.host
        self.pols[serial] = entry.polarization
        self.detector.set_name(serial, entry.name)
        self.store.set_ird_name(serial, entry.name, _now_ms())
        read, _ = self.ird_factory(entry, self.cfg)
        d = self.cfg.detect
        loop = DeviceLoop(f"ird:{serial}", 1 / self.cfg.ird.rate_hz, read,
                          lambda ts, s, serial=serial: self._on_ird(serial, ts, s),
                          lambda ts, st, detail, serial=serial: self._on_state(ts, f"ird:{serial}", st, detail),
                          d.down_after_failures, d.retry_s)
        self.ird_loops[serial] = loop
        if self._started:
            loop.start()

    def _entries(self) -> tuple:
        return tuple(IrdEntry(s, self.names[s], self.hosts[s], self.pols.get(s))
                     for s in self.ird_loops) + tuple(self.pending)

    def _save_irds(self, entries: tuple | None = None) -> None:
        """Enregistre d'abord : si config.toml ne peut pas être écrit, la mémoire ne change pas."""
        entries = self._entries() if entries is None else tuple(entries)
        if self.config_path is not None:
            save_irds(self.config_path, entries)
        self.cfg = replace(self.cfg, irds=entries)

    def identify_pending(self) -> None:
        """Identifie les IRD de l'ancien format (sans numéro de série) ; ceux qui échouent restent en attente."""
        with self._devices_lock:
            still, changed = [], False
            for entry in self.pending:
                try:
                    serial, name = self.ird_factory(entry, self.cfg)[1]()
                except Exception as e:
                    log.info("IRD %s pas encore identifié : %s", entry.host, e)
                    still.append(entry)
                    continue
                changed = True
                if serial in self.ird_loops:
                    log.info("IRD %s déjà suivi (n° %s) : entrée en double ignorée", entry.host, serial)
                    continue
                self._register(IrdEntry(serial, entry.name if entry.name not in ("", "IRD") else name or "IRD",
                                        entry.host))
            self.pending = still
            if changed:
                self._save_irds()

    def _identify_loop(self):
        while self.pending and not self._stopping.wait(self.cfg.detect.retry_s):
            self.identify_pending()

    def add_ird(self, host: str) -> IrdEntry:
        with self._devices_lock:
            serial, name = self.ird_factory(IrdEntry("", "", host), self.cfg)[1]()
            if serial in self.ird_loops:
                raise ValueError(f"cet IRD (n° {serial}) est déjà suivi")
            entry = IrdEntry(serial, name or "IRD", host)
            self._save_irds((*self._entries(), entry))  # suivi seulement une fois enregistré
            self._register(entry)
        log.info("IRD ajouté : %s (n° %s, %s)", entry.name, serial, host)
        return entry

    def update_ird(self, serial: str, name: str | None, host: str | None, polarization=UNCHANGED) -> None:
        with self._devices_lock:
            if serial not in self.ird_loops:
                raise KeyError(serial)
            if name:
                self.names[serial] = name
                self.detector.set_name(serial, name)
                self.store.set_ird_name(serial, name, _now_ms())
            if host and host != self.hosts[serial]:
                self.hosts[serial] = host
                read, _ = self.ird_factory(IrdEntry(serial, self.names[serial], host), self.cfg)
                self.ird_loops[serial].replace_reader(read)
            if polarization is not UNCHANGED:
                self.pols[serial] = polarization
                tuning = self.tunings.get(serial)
                if tuning is not None:  # regroupement à jour sans attendre la prochaine lecture
                    self.tunings[serial] = replace(tuning, polarization=polarization or self.read_pols.get(serial))
            self._save_irds()
        if polarization is not UNCHANGED:
            self._publish_carriers_if_changed(self.carriers())

    def remove_ird(self, serial: str) -> None:
        with self._devices_lock:
            loop = self.ird_loops.pop(serial, None)
            if loop is None:
                raise KeyError(serial)
            loop.stop()
            if loop.is_alive():
                loop.join(5)
            for per_ird in (self.tunings, self.details, self.hosts, self.pols, self.read_pols):
                per_ird.pop(serial, None)
            name = self.names.pop(serial, serial)
            self._save_irds()
            with self._detect_lock:
                events = self.detector.remove_ird(_now_ms(), serial)
        log.info("IRD retiré : %s (n° %s)", name, serial)
        self._publish_events(events)
        self._publish_carriers_if_changed(self.carriers())

    # --- Porteuses ---

    def carriers(self) -> list:
        # Dans l'ordre de config.toml, pour que l'ordre des IRD d'une porteuse ne dépende pas de qui a répondu en premier.
        # Copies : un IRD peut être ajouté ou retiré par un autre fil pendant le calcul.
        with self._carriers_lock:
            current, loops = dict(self.tunings), list(self.ird_loops)
            tunings = {s: current[s] for s in loops if s in current}
            carriers = group_carriers(tunings, self.cfg.carriers, previous=self._last_carriers)
            self._last_carriers = carriers
            return carriers

    def _save_carriers(self, entries: tuple) -> None:
        if self.config_path is not None:
            save_carriers(self.config_path, tuple(entries))
        self.cfg = replace(self.cfg, carriers=tuple(entries))

    def _carrier(self, key: str) -> Carrier:
        c = next((c for c in self.carriers() if c.key == key), None)
        if c is None:
            raise KeyError(key)
        return c

    def set_carrier_name(self, key: str, name: str) -> None:
        with self._devices_lock:
            c = self._carrier(key)
            if c.manual:
                entries = [replace(e, name=name) if e.manual and entry_matches(c, e) else e for e in self.cfg.carriers]
            else:
                entries = [e for e in self.cfg.carriers if not entry_matches(c, e)]
                entries.append(CarrierEntry(name, c.rf_khz / 1000, c.polarization,
                                            c.symbol_rate / 1e6 if c.symbol_rate else None, False))
            self._save_carriers(entries)
        self._publish_carriers_if_changed(self.carriers())

    def add_carrier(self, entry: CarrierEntry) -> None:
        with self._devices_lock:
            for e in self.cfg.carriers:
                rates = [r * 1e6 for r in (e.symbol_rate_msps, entry.symbol_rate_msps) if r]
                tolerance_mhz = min(rates) / 2 / 1e6 if rates else 1.0
                if e.manual and compatible_pol(e.polarization, entry.polarization) \
                        and abs(e.freq_mhz - entry.freq_mhz) < tolerance_mhz:
                    raise ValueError(f"une porteuse saisie existe déjà à cette fréquence : {e.name}")
            self._save_carriers((*self.cfg.carriers, replace(entry, manual=True)))
        self._publish_carriers_if_changed(self.carriers())

    def remove_carrier(self, key: str) -> None:
        with self._devices_lock:
            c = self._carrier(key)
            if not c.manual:
                raise ValueError("seule une porteuse saisie à la main peut être supprimée")
            self._save_carriers([e for e in self.cfg.carriers if not (e.manual and entry_matches(c, e))])
        self._publish_carriers_if_changed(self.carriers())

    def _publish_carriers_if_changed(self, carriers: list) -> None:
        with self._carriers_lock:
            # Un autre fil a calculé une liste plus récente : c'est elle qui sera publiée (la nôtre fermerait à tort
            # les alertes de ses nouvelles porteuses).
            if carriers is not self._last_carriers:
                return
            sig = tuple((c.key, c.name, c.irds, c.manual) for c in carriers)
            if sig == self._carriers_sig:
                return
            self._carriers_sig = sig
            with self._detect_lock:
                events = self.detector.prune_carriers(_now_ms(), {c.key for c in carriers})
            self.bus.publish({"type": "carriers", "carriers": [carrier_dict(c) for c in carriers]})
        self._publish_events(events)

    # --- FSH4 ---

    def set_thresholds(self, margin_drop_db: float, margin_min_db: float) -> None:
        """Seuils de marge réglés depuis la page : enregistrés, appliqués tout de suite à tous les IRD."""
        with self._devices_lock:
            if self.config_path is not None:
                save_detect(self.config_path, margin_drop_db, margin_min_db)
            self.cfg = replace(self.cfg, detect=replace(self.cfg.detect, margin_drop_db=margin_drop_db,
                                                        margin_min_db=margin_min_db))
            with self._detect_lock:
                events = self.detector.set_thresholds(_now_ms(), margin_drop_db, margin_min_db)
        log.info("Seuils de marge : chute %.1f dB, minimum %.1f dB", margin_drop_db, margin_min_db)
        self._publish_events(events)

    def set_fsh(self, host: str, port: int, polarization: str | None) -> None:
        with self._devices_lock:
            if self.config_path is not None:
                save_fsh(self.config_path, host, port, polarization)
            self.cfg = replace(self.cfg, fsh=replace(self.cfg.fsh, host=host, port=port, polarization=polarization))
            fsh_read, fsh_close = default_fsh(self.cfg)
            with self._detect_lock:
                # Le changement est d'abord demandé : une lecture de l'ancien appareil qui se termine ensuite est
                # reconnue (replacement_pending) et ignorée par la détection.
                self.fsh_loop.replace_reader(fsh_read, fsh_close)
                events = self.detector.on_devices_changed(_now_ms(), f"FSH4 {host}:{port}")
                self._fsh_key = None
        log.info("FSH4 : %s:%d, entrée %s", host, port, polarization or "polarisation inconnue")
        self._publish_events(events)

    # --- Mesures ---

    def _publish_events(self, events):
        for ev in events:
            log.info("Événement %s %s : %s", ev["kind"], "ouvert" if ev["ts_end"] is None else "fermé", ev["detail"])
            self.bus.publish({"type": "event", **ev})

    def _reachable(self) -> set:
        return {s for s, loop in list(self.ird_loops.items()) if loop.state == "up"}

    def devices(self) -> dict:
        """FSH4 et IRD suivis, pour la fenêtre « Appareils… »."""
        loops, names, hosts = list(self.ird_loops.items()), dict(self.names), dict(self.hosts)
        pols, tunings = dict(self.pols), dict(self.tunings)
        by_serial = {s: c.key for c in self.carriers() for s in c.irds}
        return {
            "fsh": {"host": self.cfg.fsh.host, "port": self.cfg.fsh.port, "polarization": self.cfg.fsh.polarization},
            "irds": [{"serial": s, "name": names.get(s, s), "host": hosts.get(s, ""), "state": loop.state,
                      "carrier": by_serial.get(s), "polarization": pols.get(s),
                      "effective_polarization": tunings[s].polarization if s in tunings else pols.get(s)}
                     for s, loop in loops],
        }

    def _on_ird(self, serial, ts, reading: IrdReading):
        if serial not in self.ird_loops:
            return  # lecture arrivée après le retrait de l'IRD
        s, health = reading.sample, reading.health
        self.read_pols[serial] = s.polarization
        self.details[serial] = s.details
        if self.pols.get(serial):  # la polarisation imposée par l'opérateur prime sur celle lue
            s = replace(s, polarization=self.pols[serial])
        self.store.add_ird(ts, serial, s)
        if health is not None:
            self.store.add_health(ts, serial, health)
        if s.rf_khz:
            sr, roll_off = s.symbol_rate, s.roll_off
            prev = self.tunings.get(serial)
            # Décrochage sur la même porteuse (à moins d'un demi-débit symbole) : débit symbole et roll-off du dernier lock.
            if not s.locked and prev and prev.symbol_rate and abs(s.rf_khz - prev.rf_khz) * 2000 < prev.symbol_rate:
                sr, roll_off = sr or prev.symbol_rate, roll_off if roll_off is not None else prev.roll_off
            self.tunings[serial] = Tuning(s.rf_khz, s.polarization, sr, roll_off, measured=s.rf_measured is not False)
        name = self.names.get(serial, serial)
        carriers = self.carriers()
        with self._detect_lock:
            if serial not in self.ird_loops:
                # Retiré pendant le traitement (remove_ird a déjà nettoyé la détection) : rien ne doit renaître.
                self.tunings.pop(serial, None)
                self.details.pop(serial, None)
                return
            events = self.detector.on_ird(ts, serial, name, s)
            if health is not None:
                events += self.detector.on_health(ts, serial, health)
            if reading.logs is not None:
                events += self.detector.on_logs(ts, serial, reading.logs)
            events += self.detector.on_alone(ts, carriers, self._reachable() | {serial})
        self.bus.publish({"type": "ird", "ts": ts, "serial": serial, "name": name, **asdict(s), "state": band_state(s),
                          "health": None if health is None else {"ts": ts, **asdict(health)}})
        self._publish_events(events)
        self._publish_carriers_if_changed(carriers)

    def _on_marker(self, ts, reading):
        self.store.add_markers(ts, reading.markers)
        freqs = {n: m.freq_hz for n, m in reading.markers.items()}
        carriers = self.carriers()
        mm = attach_markers(carriers, freqs, self.cfg.fsh.polarization, self.cfg.detect.noise_markers)
        self.marker_freqs, self.marker_map = freqs, mm
        events = []
        with self._detect_lock:
            if self.fsh_loop.replacement_pending():
                return  # lecture de l'ancien FSH4, arrivée après le changement d'appareil
            if reading.settings is not None:
                key = reading.settings
                if self._fsh_key is not None and key != self._fsh_key:
                    events += self.detector.on_fsh_settings(ts, describe_settings(key))
                self._fsh_key = key
            for c in carriers:
                n = center_marker(c, mm, freqs)
                if n is not None:
                    events += self.detector.on_carrier_level(ts, c, reading.markers[n].level_dbm)
            noise = [reading.markers[n].level_dbm for n in mm.noise if n in reading.markers]
            if noise and not mm.if_warning:
                events += self.detector.on_noise(ts, sum(noise) / len(noise))
        self.bus.publish({"type": "markers", "ts": ts,
                          "markers": {n: asdict(m) for n, m in reading.markers.items()}})
        self._publish_events(events)

    def _on_state(self, ts, device, state, detail):
        with self._detect_lock:
            # Lecture terminée après le retrait de l'IRD (un IRD injoignable met longtemps à échouer) : rien ne doit
            # renaître, ni état ni « injoignable » que plus rien ne fermerait.
            if device.startswith("ird:") and device[4:] not in self.ird_loops:
                return
            log.info("%s : %s %s", device.upper(), state, detail)
            self.store.add_status(ts, device, state, detail)
            events = self.detector.on_state(ts, device, state, detail)
        self.bus.publish({"type": "state", "ts": ts, "device": device, "state": state, "detail": detail})
        self._publish_events(events)

    # --- État et cycle de vie ---

    def carriers_view(self) -> list:
        """Porteuses actuelles pour la page, avec les IRD dont la polarisation n'est ni imposée ni lue."""
        mm, tunings = self.marker_map, dict(self.tunings)
        return [{**carrier_view(c, mm, self.marker_freqs),
                 "unknown_polarization": [s for s in c.irds if s in tunings and tunings[s].polarization is None]}
                for c in self.carriers()]

    def status(self) -> dict:
        srv, det = self.cfg.server, self.cfg.detect
        latest = self.store.latest()
        carriers = self.carriers_view()
        mm = self.marker_map
        loops, names, hosts = list(self.ird_loops.items()), dict(self.names), dict(self.hosts)
        details = dict(self.details)
        fsh_settings = self._fsh_key

        def with_details(serial):
            row = latest["irds"].get(serial)
            d = details.get(serial)
            return {**row, "details": asdict(d) if d else None} if row else None

        return {
            "marker": latest["marker"],
            "markers": latest["markers"],
            "fsh": {"host": self.cfg.fsh.host, "port": self.cfg.fsh.port,
                    "polarization": self.cfg.fsh.polarization, "state": self.fsh_loop.state,
                    # Dernier réglage lu (None tant que le FSH4 n'a pas répondu).
                    "span_hz": fsh_settings.span_hz if fsh_settings else None,
                    "rbw_hz": fsh_settings.rbw_hz if fsh_settings else None},
            "irds": {serial: {"name": names.get(serial, serial), "host": hosts.get(serial, ""), "state": loop.state,
                              "latest": with_details(serial)}
                     for serial, loop in loops},
            "pending": [asdict(e) for e in self.pending],
            "carriers": carriers,
            "marker_map": {"if_warning": mm.if_warning, "noise": list(mm.noise), "ambiguous": list(mm.ambiguous)},
            "devices": latest["devices"],
            "config": {"live_window_min": srv.live_window_min, "event_padding_s": srv.event_padding_s,
                       "max_points": srv.max_points, "margin_drop_db": det.margin_drop_db,
                       "margin_min_db": det.margin_min_db, "median_window_s": det.median_window_s,
                       "drop_confirm_s": det.drop_confirm_s},
            "now": _now_ms(),
        }

    def purge(self) -> None:
        days = self.cfg.paths.retention_days
        if days > 0:
            self.store.purge_before(int((time.time() - days * 86_400) * 1000))
            log.info("Purge des données de plus de %d jour(s)", days)

    def _purge_loop(self):
        # Première purge dès le démarrage, mais dans ce fil : une grosse purge ne retarde pas la collecte.
        while True:
            try:
                self.purge()
            except Exception:
                log.exception("Échec de la purge")
            if self._stopping.wait(PURGE_EVERY_S):
                return

    def _close_open_events(self) -> int:
        """Événements restés ouverts (arrêt du service) : fermés à la dernière mesure enregistrée."""
        last = self.store.last_ts()
        return 0 if last is None else self.store.close_open_events(last)

    def start(self) -> None:
        n = self._close_open_events()
        if n:
            log.info("%d événement(s) resté(s) ouvert(s) fermé(s) à la dernière mesure", n)
        if self.cfg.paths.retention_days > 0:
            self._purger.start()
        self._started = True
        self.fsh_loop.start()
        for loop in list(self.ird_loops.values()):
            loop.start()
        if self.pending:
            self.identify_pending()
            self._identifier.start()

    def stop(self) -> None:
        self._stopping.set()
        loops = [self.fsh_loop, *self.ird_loops.values()]
        for loop in loops:
            loop.stop()
        for loop in loops:
            if loop.is_alive():
                loop.join(5)
        if self.fsh_loop.close is not None:
            self.fsh_loop.close()
        self._close_open_events()
        self.store.close()
