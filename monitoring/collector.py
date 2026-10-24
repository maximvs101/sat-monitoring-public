"""Boucle de collecte d'un appareil : cadence, horodatage, états up/down/paused."""

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

PAUSED_POLL_S = 0.5
MAX_BUSY = 10  # au-delà, un appareil « occupé » compte comme en échec


class DeviceBusy(Exception):
    """L'appareil répond mais ne peut pas servir la mesure pour l'instant (pas un échec)."""


class DeviceLoop(threading.Thread):
    """Lit un appareil à cadence fixe dans son propre fil. `close` ferme sa connexion (pause, changement d'appareil,
    arrêt). Horodatages pris sur `clock` (heure du système), durées et attentes sur `timer` (horloge monotone :
    un changement d'heure ne bloque pas la collecte)."""

    def __init__(self, device: str, period_s: float, read: Callable[[], object],
                 on_sample: Callable[[int, object], None], on_state: Callable[[int, str, str], None],
                 down_after: int, retry_s: float, close: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.time, timer: Callable[[], float] = time.monotonic,
                 min_idle_s: float = 0.0):
        super().__init__(name=f"collecte-{device}", daemon=True)
        self.device = device
        self.period_s = period_s
        self.read = read
        self.on_sample = on_sample
        self.on_state = on_state
        self.down_after = down_after
        self.retry_s = retry_s
        self.close = close
        self.clock = clock
        self.timer = timer
        self.min_idle_s = min_idle_s
        self.state = "unknown"
        self.failures = 0
        self.busy = 0
        self._paused = threading.Event()
        self._pause_applied = False
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._replacement = None
        self._replacement_lock = threading.Lock()

    def replacement_pending(self) -> bool:
        """Un changement d'appareil attend la prochaine itération : la lecture en cours vient de l'ancien."""
        return self._replacement is not None

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _set_state(self, ts: int, state: str, detail: str) -> None:
        self.state = state
        self.on_state(ts, state, detail)

    def _reset_counts(self) -> None:
        self.failures = 0
        self.busy = 0

    def step(self) -> float:
        """Une lecture ; renvoie l'attente avant la suivante (sans rattrapage)."""
        with self._replacement_lock:
            replacement, self._replacement = self._replacement, None
        if replacement is not None:
            # Changement d'appareil, appliqué ici : l'ancienne connexion est fermée entre deux lectures.
            if self.close is not None:
                self.close()
            self.read, self.close = replacement
            self._reset_counts()
            # En pause, elle le reste : la nouvelle connexion ne s'ouvrira qu'à la reprise.
            self._set_state(self._now_ms(), "paused" if self._paused.is_set() else "unknown", "")
        if self._paused.is_set():
            # Appliquée ici, dans le thread de collecte : jamais de socket fermée en pleine lecture.
            if not self._pause_applied:
                self._pause_applied = True
                if self.close is not None:
                    self.close()
                self._set_state(self._now_ms(), "paused", "")
            return PAUSED_POLL_S
        # Pas en pause : la prochaine pause devra s'appliquer (même si une reprise est arrivée au mauvais moment).
        self._pause_applied = False
        m0, t0 = self.timer(), self.clock()
        try:
            value = self.read()
            error = None
        except DeviceBusy as e:
            self.busy += 1
            if self.busy <= MAX_BUSY:
                log.debug("%s : occupé (%s)", self.device, e)
                return self.period_s
            error = e
        except Exception as e:  # tout échec de lecture compte, sans arrêter la boucle
            error = e
        m1, t1 = self.timer(), self.clock()
        ts = int((t0 + t1) / 2 * 1000)
        wait = max(self.period_s - (m1 - m0), self.min_idle_s)

        if error is None:
            self._reset_counts()
            self.on_sample(ts, value)
            if self.state != "up" and not self._paused.is_set():
                self._set_state(ts, "up", "")
            # Le traitement de la mesure (base, détection) compte dans la période : la cadence reste celle réglée.
            return max(self.period_s - (self.timer() - m0), self.min_idle_s)

        self.failures += 1
        log.debug("%s : échec %d (%s)", self.device, self.failures, error)
        if self.failures >= self.down_after:
            if self.state != "down":
                self._set_state(ts, "down", str(error))
            return self.retry_s
        return wait

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                wait = self.step()
            except Exception:  # un callback en erreur ne doit pas tuer la collecte
                log.exception("%s : erreur dans la boucle de collecte", self.device)
                wait = self.retry_s
            self._wake.wait(wait)
            self._wake.clear()

    def pause(self) -> None:
        """Demande la pause ; la boucle l'applique à sa prochaine itération."""
        self._paused.set()
        self._wake.set()

    def replace_reader(self, read: Callable[[], object], close: Callable[[], None] | None = None) -> None:
        """Change d'appareil ; la boucle l'applique à sa prochaine itération."""
        with self._replacement_lock:
            self._replacement = (read, close)
        self._wake.set()

    def resume(self) -> None:
        self._reset_counts()
        self._paused.clear()
        self._pause_applied = False
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
