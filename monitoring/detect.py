"""Détection d'événements à partir des mesures FSH4 et IRD."""

import statistics
from dataclasses import replace
from collections import deque

from .carriers import Carrier
from .config import DetectConfig
from .ird import IrdHealth, IrdLogMessage, IrdSample
from .store import Store

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
SETTINGS_REASON = "réglage FSH4 modifié"
DEVICES_REASON = "appareils changés"
REMOVED_REASON = "IRD retiré"
VANISHED_REASON = "porteuse disparue"
ALONE_DELAY_MS = 3000
DECODE_DELAY_MS = 3000
RECEPTION_LOST_REASON = "réception perdue"
DOWN_REASON = "IRD injoignable"
LOG_RESET_REASON = "journal IRD réinitialisé"
THRESHOLD_REASON = "seuil modifié"
LOW_RECOVER_DB = 0.5  # « Marge basse » se ferme 0,5 dB au-dessus du minimum (pas de battement autour du seuil)
RECEPTION_CHANGED_REASON = "réception modifiée (modulation, fréquence ou débit symbole)"
DECODE_SEVERITY = {"video_lost": CRITICAL, "audio_silent": WARNING, "not_descrambled": CRITICAL,
                   "ts_stopped": CRITICAL}
# Dégradations du décodage comparées entre IRD du même programme pour « IRD seul en défaut ».
PROGRAM_KINDS = ("video_lost", "not_descrambled")
LOG_SEVERITY = {"information": INFO, "warning": WARNING}


def _decode_unknown(s: IrdSample) -> set:
    """Alertes de décodage dont la condition ne peut être jugée (champ absent de la réponse)."""
    out = set()
    if s.video_active is None:
        out.add("video_lost")
    if s.video_active is None or s.audio_configured is None or (s.audio_configured and s.audio_active is None):
        out.add("audio_silent")
    if s.scrambled is None or (s.scrambled and s.descrambled is None):
        out.add("not_descrambled")
    if s.bitrate_kbps is None:
        out.add("ts_stopped")
    return out


def _decode_conditions(s: IrdSample) -> dict:
    """Alertes de décodage dont la condition est vraie (démodulateur et flux transport verrouillés)."""
    out = {}
    if s.video_active is False:
        out["video_lost"] = "image perdue"
    if s.audio_configured and s.audio_active == 0 and s.video_active:
        out["audio_silent"] = f"son absent ({s.audio_active}/{s.audio_configured} voies)"
    if s.scrambled and s.descrambled is False:
        out["not_descrambled"] = "programme non déchiffré"
    if s.bitrate_kbps == 0:
        out["ts_stopped"] = "flux d'entrée arrêté"
    return out


def _fr(value: float, unit: str) -> str:
    return f"{value:.1f}".replace(".", ",") + f" {unit}"


class _DropRule:
    """Écart d'une valeur par rapport à la médiane glissante (chute, ou montée si rise=True) ;
    référence figée à l'ouverture."""

    def __init__(self, kind, label, unit, drop, recover, cfg: DetectConfig, rise=False, source=None):
        self.kind, self.label, self.unit = kind, label, unit
        self.prefix, self.source = "", source  # « IRD 1 : » / « BBC : » devant le détail ; source de l'événement
        self.sign = -1 if rise else 1  # on travaille toujours sur une « chute » de sign × valeur
        self.drop, self.recover = drop, recover
        self.window_ms = cfg.median_window_s * 1000
        self.min_history_ms = cfg.min_history_s * 1000
        self.confirm_ms = cfg.drop_confirm_s * 1000
        self.values = deque()
        self.event_id = None
        self.reference = None
        # Périodes écartées de la référence : chaque alerte, et la minute qui suit sa fermeture.
        self.excluded = []  # [début, fin] en ms ; fin à None tant que l'alerte est ouverte

    def _excluded(self, t):
        return any(start <= t and (end is None or t <= end) for start, end in self.excluded)

    def check(self, det, ts, value):
        if value is None:
            return []
        # Purger d'abord : après une pause ou une coupure, la référence ne doit pas être périmée.
        while self.values and self.values[0][0] < ts - self.window_ms:
            self.values.popleft()
        self.values.append((ts, value))
        # Les dernières secondes (médiane, donc insensible aux points isolés) contre la référence d'avant.
        cut = ts - self.confirm_ms
        recent = [(t, v) for t, v in self.values if t > cut] or [(ts, value)]
        level = statistics.median(v for _, v in recent)
        changed = []
        s = self.sign
        if self.event_id is not None:
            if s * level >= s * self.reference - self.recover:
                changed.append(det._close(self.event_id, ts))
                self.event_id = None
                self.excluded[-1][1] = ts + self.window_ms
            return changed
        self.excluded = [iv for iv in self.excluded if iv[1] is None or iv[1] >= ts - self.window_ms]
        before = [(t, v) for t, v in self.values
                  if t <= cut and t < recent[0][0] and not self._excluded(t)]
        if self.excluded and self.reference is not None:
            # Dans la minute qui suit une alerte, la référence reste celle d'avant l'alerte.
            median = self.reference
        elif before and before[-1][0] - before[0][0] >= self.min_history_ms:
            median = statistics.median(v for _, v in before)
        else:
            median = None
        if median is not None:
            if s * level < s * median - self.drop:
                self.reference = median
                detail = f"{self.prefix}{self.label} {_fr(level, self.unit)} (référence {_fr(median, self.unit)})"
                self.event_id, ev = det._open(recent[0][0], self.kind, WARNING, detail, self.source)
                self.excluded.append([recent[0][0], None])
                changed.append(ev)
        return changed

    def reset(self, det, ts, reason):
        """Oublie la référence (nouveau réglage) ; ferme l'alerte en cours avec le motif."""
        changed = []
        if self.event_id is not None:
            changed.append(det._close_with_reason(self.event_id, ts, reason))
            self.event_id = None
        self.values.clear()
        self.excluded.clear()
        return changed


class _IrdRules:
    """État de détection d'un IRD : réception (lock, lock TS, BER, chute de marge, marge basse), décodage
    (image, son, déchiffrement, flux), matériel, journal, et « seul en défaut »."""

    def __init__(self, serial: str, name: str, cfg: DetectConfig):
        self.serial, self.name = serial, name
        self.source = f"ird:{serial}"
        self.margin = _DropRule("margin_drop", "marge", "dB", cfg.margin_drop_db, cfg.margin_recover_db, cfg,
                                source=self.source)
        self.lock_event = None
        self.ts_lock_event = None
        self.ber_event = None
        self.last_ber_high = None
        self.alone_event = None
        self.low_event = None   # « Marge basse » (sous le minimum fixe)
        self.low_since = None
        self.degraded_since = None  # début de la dégradation de réception en cours
        self.decode = {}        # type d'alerte de décodage → événement ouvert
        self.decode_since = {}  # type d'alerte de décodage → début de la condition
        self.program = None     # programme décodé à la dernière lecture
        self.reception = None   # (modulation, débit symbole, fréquence RF) de la dernière lecture verrouillée
        self.program_since = None  # début de la dégradation réception ou image/déchiffrement en cours
        self.hw_event = None
        self.log_events = {}    # nId → événement ouvert
        self.log_ids = None     # nId vus au dernier relevé (None : journal jamais lu)
        self.log_max = None
        self.rename(name)

    def rename(self, name: str) -> None:
        self.name = name
        self.margin.prefix = f"{name} : "

    def reception_events(self) -> list:
        return [e for e in (self.lock_event, self.ts_lock_event, self.ber_event, self.margin.event_id, self.low_event)
                if e is not None]

    def program_events(self) -> list:
        return self.reception_events() + [self.decode[k] for k in PROGRAM_KINDS if k in self.decode]


class Detector:
    def __init__(self, store: Store, cfg: DetectConfig):
        self.store = store
        self.cfg = cfg
        self.irds = {}           # numéro de série → _IrdRules
        self.carrier_rules = {}  # clé de porteuse → _DropRule (chute du niveau RF)
        self.noise = _DropRule("noise_rise", "plancher de bruit", "dBm", cfg.noise_rise_db, cfg.noise_recover_db, cfg,
                               rise=True, source="fsh")
        self.down_events = {}    # appareil (« fsh » ou « ird:<série> ») → événement « injoignable »
        self.names = {}          # numéro de série → nom (connu même avant la première lecture)

    def set_name(self, serial: str, name: str) -> None:
        self.names[serial] = name
        if serial in self.irds:
            self.irds[serial].rename(name)

    def _open(self, ts, kind, severity, detail, source=None):
        event_id = self.store.open_event(ts, kind, severity, detail, source)
        return event_id, self.store.event(event_id)

    def _close(self, event_id, ts):
        self.store.close_event(event_id, ts)
        return self.store.event(event_id)

    def _close_with_reason(self, event_id, ts, reason):
        detail = self.store.event(event_id)["detail"]
        self.store.close_event(event_id, ts, f"{detail} — {reason}")
        return self.store.event(event_id)

    def _rules(self, serial: str, name: str) -> _IrdRules:
        rules = self.irds.get(serial)
        if rules is None:
            rules = self.irds[serial] = _IrdRules(serial, name, self.cfg)
        if rules.name != name:
            rules.rename(name)
        return rules

    def _close_decode(self, r: _IrdRules, ts: int, reason: str) -> list[dict]:
        changed = [self._close_with_reason(e, ts, reason) for e in r.decode.values()]
        r.decode.clear()
        r.decode_since.clear()
        return changed

    def _check_decode(self, r: _IrdRules, ts: int, s: IrdSample) -> list[dict]:
        if not s.locked or not s.ts_locked:
            return self._close_decode(r, ts, RECEPTION_LOST_REASON)
        active = _decode_conditions(s)
        unknown = _decode_unknown(s)
        changed = []
        for kind in DECODE_SEVERITY:
            if kind in unknown:
                continue  # inconnu n'est pas « revenu » : alerte et délai restent en l'état
            if kind in active:
                since = r.decode_since.setdefault(kind, ts)
                if kind not in r.decode and ts - since >= DECODE_DELAY_MS:
                    r.decode[kind], ev = self._open(ts, kind, DECODE_SEVERITY[kind], f"{r.name} : {active[kind]}",
                                                    r.source)
                    changed.append(ev)
            else:
                r.decode_since.pop(kind, None)  # délai interrompu : repart de zéro
                if kind in r.decode:
                    changed.append(self._close(r.decode.pop(kind), ts))
        return changed

    def _close_low(self, r: _IrdRules, ts: int, reason: str) -> list[dict]:
        r.low_since = None
        if r.low_event is None:
            return []
        ev = self._close_with_reason(r.low_event, ts, reason)
        r.low_event = None
        return [ev]

    def _check_low(self, r: _IrdRules, ts: int, margin) -> list[dict]:
        """« Marge basse » : sous le minimum fixe depuis drop_confirm_s ; fermée 0,5 dB au-dessus."""
        minimum = self.cfg.margin_min_db
        if not minimum or margin is None:
            return []
        if r.low_event is None:
            if margin >= minimum:
                r.low_since = None
                return []
            r.low_since = r.low_since if r.low_since is not None else ts
            if ts - r.low_since < self.cfg.drop_confirm_s * 1000:
                return []
            detail = f"{r.name} : marge {_fr(margin, 'dB')} sous le minimum de {_fr(minimum, 'dB')}"
            r.low_event, ev = self._open(ts, "margin_low", WARNING, detail, r.source)
            return [ev]
        if margin >= minimum + LOW_RECOVER_DB:
            ev = self._close(r.low_event, ts)
            r.low_event, r.low_since = None, None
            return [ev]
        return []

    def set_thresholds(self, ts: int, margin_drop_db: float, margin_min_db: float) -> list[dict]:
        """Seuils de marge réglés depuis la page, appliqués à tous les IRD sans redémarrer."""
        self.cfg = replace(self.cfg, margin_drop_db=margin_drop_db, margin_min_db=margin_min_db)
        changed = []
        for r in self.irds.values():
            r.margin.drop = margin_drop_db
            changed += self._close_low(r, ts, THRESHOLD_REASON)  # rouverte dans 3 s si toujours sous le minimum
        return changed

    def _check_reception_change(self, r: _IrdRules, ts: int, s: IrdSample) -> list[dict]:
        """Autre modulation, autre débit symbole ou autre porteuse : la marge n'est plus comparable à sa
        référence (une chute ouverte ne se refermerait jamais). Un champ absent d'une lecture ne compte pas."""
        prev = r.reception or (None, None, None)
        cur = tuple(c if c is not None else p for c, p in zip((s.modcod, s.symbol_rate, s.rf_khz), prev))
        r.reception = cur
        tol_khz = (cur[1] or 2_000_000) / 2 / 1000  # moitié du débit symbole : une autre porteuse
        if prev[0] is None and prev[1] is None and prev[2] is None:
            return []
        if (any(p is not None and c != p for p, c in zip(prev[:2], cur[:2]))
                or (prev[2] is not None and abs(cur[2] - prev[2]) > tol_khz)):
            return r.margin.reset(self, ts, RECEPTION_CHANGED_REASON)
        return []

    def _check_locked(self, r: _IrdRules, ts: int, s: IrdSample) -> list[dict]:
        """Démodulateur verrouillé : lock retrouvé, lock TS, BER, chute de marge, marge basse."""
        changed = []
        if r.lock_event is not None:
            changed.append(self._close(r.lock_event, ts))
            r.lock_event = None
        if not s.ts_locked and r.ts_lock_event is None:
            r.ts_lock_event, ev = self._open(ts, "ts_lock_lost", CRITICAL, f"{r.name} : flux transport déverrouillé",
                                             r.source)
            changed.append(ev)
        elif s.ts_locked and r.ts_lock_event is not None:
            changed.append(self._close(r.ts_lock_event, ts))
            r.ts_lock_event = None
        if s.ber is not None:
            if s.ber > self.cfg.ber_threshold:
                r.last_ber_high = ts
                if r.ber_event is None:
                    r.ber_event, ev = self._open(ts, "ber_high", WARNING, f"{r.name} : BER {s.ber:.1e}", r.source)
                    changed.append(ev)
            elif r.ber_event is not None and ts - r.last_ber_high >= self.cfg.ber_clear_s * 1000:
                changed.append(self._close(r.ber_event, ts))
                r.ber_event = None
        changed += r.margin.check(self, ts, s.margin_db)
        changed += self._check_low(r, ts, s.margin_db)
        return changed

    def on_ird(self, ts: int, serial: str, name: str, s: IrdSample) -> list[dict]:
        self.names[serial] = name
        r = self._rules(serial, name)
        changed = self._check_decode(r, ts, s)
        r.program = s.program_number
        if s.locked:
            changed += self._check_reception_change(r, ts, s)
            changed += self._check_locked(r, ts, s)
        else:
            # Déverrouillé : C/N, marge et BER n'ont plus de sens, seul lock_lost compte.
            if r.lock_event is None:
                r.lock_event, ev = self._open(ts, "lock_lost", CRITICAL, f"{r.name} : démodulateur déverrouillé",
                                              r.source)
                changed.append(ev)
            changed += self._close_low(r, ts, RECEPTION_LOST_REASON)
        r.degraded_since = (r.degraded_since or ts) if r.reception_events() else None
        r.program_since = (r.program_since or ts) if r.program_events() else None
        return changed

    def on_health(self, ts: int, serial: str, h: IrdHealth) -> list[dict]:
        """État matériel : surchauffe, ventilateur, alimentation."""
        r = self._rules(serial, self.names.get(serial, serial))
        faults = [label for flag, label in ((h.overheat, "surchauffe"), (h.fan_failure, "ventilateur"),
                                            (h.power_failure, "alimentation")) if flag]
        if faults and r.hw_event is None:
            r.hw_event, ev = self._open(ts, "hw_fault", CRITICAL, f"{r.name} : {', '.join(faults)}", r.source)
            return [ev]
        if not faults and r.hw_event is not None:
            ev = self._close(r.hw_event, ts)
            r.hw_event = None
            return [ev]
        return []

    def on_logs(self, ts: int, serial: str, messages: list[IrdLogMessage]) -> list[dict]:
        """Journal de l'IRD : chaque nouveau message devient un événement (déjà fermé s'il est informatif)."""
        r = self._rules(serial, self.names.get(serial, serial))
        ids = {m.n_id for m in messages}
        changed = []
        for n_id in [n for n in r.log_events if n not in ids]:  # message disparu du journal
            changed.append(self._close(r.log_events.pop(n_id), ts))
        # Les nId de l'IRD sont croissants : un nId nouveau sous le plus grand déjà vu trahit un journal remis à zéro.
        if r.log_ids is None or (r.log_max is not None and any(n < r.log_max for n in ids - r.log_ids)):
            # Premier relevé, ou journal repart de zéro (IRD redémarré) : on mémorise sans créer d'événement ;
            # les messages d'avant ne veulent plus rien dire.
            changed += [self._close_with_reason(e, ts, LOG_RESET_REASON) for e in r.log_events.values()]
            r.log_events.clear()
            r.log_ids, r.log_max = ids, max(ids, default=None)
            return changed
        for m in messages:
            if m.n_id in r.log_events and m.ended:
                changed.append(self._close(r.log_events.pop(m.n_id), ts))
            elif m.n_id not in r.log_ids:
                severity = LOG_SEVERITY.get(m.level, CRITICAL)
                text = m.details or f"message sans texte ({m.level})"
                event_id, ev = self._open(ts, "ird_log", severity, f"{r.name} : {text}", r.source)
                if severity == INFO or m.ended:
                    ev = self._close(event_id, ts)
                else:
                    r.log_events[m.n_id] = event_id
                changed.append(ev)
        r.log_ids = ids
        r.log_max = max([*ids, r.log_max] if r.log_max is not None else ids, default=None)
        return changed

    def on_alone(self, ts: int, carriers: list, reachable: set) -> list[dict]:
        """« IRD seul en défaut » : sur une porteuse d'au moins 2 IRD joignables, un seul se dégrade
        depuis au moins 3 s pendant que les autres tiennent. La réception se compare entre tous les IRD
        de la porteuse ; l'image et le déchiffrement seulement entre IRD du même programme."""
        should = {}  # série → (porteuse, IRD normaux)
        for c in carriers:
            members = [m for m in c.irds if m in reachable and m in self.irds]
            groups = [(members, "reception_events", "degraded_since")]
            programs = {self.irds[m].program for m in members} - {None}
            groups += [([m for m in members if self.irds[m].program == p], "program_events", "program_since")
                       for p in programs]
            for group, events, since in groups:
                if len(group) < 2:
                    continue
                degraded = [m for m in group if getattr(self.irds[m], events)()]
                if len(degraded) == 1:
                    m = degraded[0]
                    if m not in should and ts - getattr(self.irds[m], since) >= ALONE_DELAY_MS:
                        should[m] = (c, [x for x in group if x != m])
        changed = []
        for serial, r in self.irds.items():
            if serial in should and r.alone_event is None:
                carrier, normal = should[serial]
                others = ", ".join(self.names.get(x, x) for x in normal)
                detail = (f"{self.names.get(serial, r.name)} seul en défaut sur {carrier.name} ({others} normal)"
                          " — cause probable : équipement ou câblage")
                severity = CRITICAL if r.lock_event is not None or any(k in r.decode for k in PROGRAM_KINDS) \
                    else WARNING
                r.alone_event, ev = self._open(ts, "ird_alone", severity, detail, r.source)
                changed.append(ev)
            elif serial not in should and r.alone_event is not None:
                changed.append(self._close(r.alone_event, ts))
                r.alone_event = None
        return changed

    def remove_ird(self, ts: int, serial: str) -> list[dict]:
        """IRD retiré de la surveillance : toutes ses alertes sont fermées, son état oublié."""
        changed = []
        r = self.irds.pop(serial, None)
        if r is not None:
            for event_id in (r.lock_event, r.ts_lock_event, r.ber_event, r.alone_event, r.hw_event, r.low_event,
                             *r.decode.values(), *r.log_events.values()):
                if event_id is not None:
                    changed.append(self._close_with_reason(event_id, ts, REMOVED_REASON))
            changed += r.margin.reset(self, ts, REMOVED_REASON)
        down = self.down_events.pop(f"ird:{serial}", None)
        if down is not None:
            changed.append(self._close_with_reason(down, ts, REMOVED_REASON))
        return changed

    def on_carrier_level(self, ts: int, carrier: Carrier, level_dbm: float) -> list[dict]:
        """Niveau RF d'une porteuse (son marqueur central)."""
        rule = self.carrier_rules.get(carrier.key)
        if rule is None:
            rule = self.carrier_rules[carrier.key] = _DropRule(
                "marker_drop", "niveau marqueur", "dBm", self.cfg.marker_drop_db, self.cfg.marker_recover_db,
                self.cfg, source=f"porteuse:{carrier.key}")
        rule.prefix = f"{carrier.name} : "
        return rule.check(self, ts, level_dbm)

    def prune_carriers(self, ts: int, keys: set) -> list[dict]:
        """Oublie les porteuses qui n'existent plus et ferme leurs alertes RF."""
        changed = []
        for key in [k for k in self.carrier_rules if k not in keys]:
            changed += self.carrier_rules.pop(key).reset(self, ts, VANISHED_REASON)
        return changed

    def on_noise(self, ts: int, level_dbm: float) -> list[dict]:
        """Niveau moyen des marqueurs placés sur le plancher de bruit."""
        return self.noise.check(self, ts, level_dbm)

    def _reset_rf(self, ts: int, reason: str) -> list[dict]:
        changed = []
        for rule in self.carrier_rules.values():
            changed += rule.reset(self, ts, reason)
        return changed + self.noise.reset(self, ts, reason)

    def on_devices_changed(self, ts: int, detail: str) -> list[dict]:
        """Nouvelle adresse du FSH4 : ses alertes sont fermées, ses références repartent de zéro."""
        changed = self._reset_rf(ts, DEVICES_REASON)
        down = self.down_events.pop("fsh", None)
        if down is not None:
            changed.append(self._close_with_reason(down, ts, DEVICES_REASON))
        event_id, _ = self._open(ts, "devices_changed", INFO, detail, "fsh")
        changed.append(self._close(event_id, ts))
        return changed

    def on_fsh_settings(self, ts: int, detail: str) -> list[dict]:
        """Marqueur, span ou RBW du FSH4 changé : les niveaux ne sont plus comparables."""
        changed = self._reset_rf(ts, SETTINGS_REASON)
        event_id, _ = self._open(ts, "fsh_settings", INFO, detail, "fsh")
        changed.append(self._close(event_id, ts))
        return changed

    def on_state(self, ts: int, device: str, state: str, detail: str) -> list[dict]:
        """`device` vaut « fsh » ou « ird:<série> »."""
        if state == "down" and device not in self.down_events:
            label = "fsh"
            if device.startswith("ird:"):
                # Nom de l'IRD et motif (« autre appareil à cette adresse », délai…) dans le détail.
                label = self.names.get(device[4:], device[4:])
                if detail:
                    label = f"{label} — {detail}"
            self.down_events[device], ev = self._open(ts, "device_down", CRITICAL, label, device)
            changed = [ev]
            r = self.irds.get(device[4:]) if device.startswith("ird:") else None
            if r is not None:
                # Plus de mesure : image, son, flux et matériel ne sont plus connus.
                changed += self._close_decode(r, ts, DOWN_REASON)
                if r.hw_event is not None:
                    changed.append(self._close_with_reason(r.hw_event, ts, DOWN_REASON))
                    r.hw_event = None
            return changed
        if state == "up" and device in self.down_events:
            return [self._close(self.down_events.pop(device), ts)]
        return []
