"""Client SCPI du R&S FSH4, en lecture seule (aucun réglage modifié) : span, RBW, et fréquence et niveau des
marqueurs allumés (jusqu'à 6)."""

import socket
import time
from dataclasses import dataclass

from .config import FshConfig


MARKER_NUMBERS = range(1, 7)


class FshError(Exception):
    pass


@dataclass(frozen=True)
class MarkerSample:
    freq_hz: float
    level_dbm: float


@dataclass(frozen=True)
class FshSettings:
    span_hz: float
    rbw_hz: float
    markers: tuple = ()  # ((numéro, fréquence en Hz), …) des marqueurs allumés


@dataclass(frozen=True)
class FshReading:
    markers: dict  # {numéro: MarkerSample}
    settings: FshSettings | None


class FshClient:
    def __init__(self, cfg: FshConfig):
        self.cfg = cfg
        self._sock = None

    def connect(self) -> None:
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=self.cfg.timeout_s)
            except OSError as e:
                raise FshError(f"FSH4 injoignable : {e}") from e

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def query(self, cmd: str) -> str:
        """Envoie une requête et lit la ligne de réponse. Toute erreur ferme la connexion,
        pour qu'une réponse tardive ne soit jamais prise pour celle de la requête suivante."""
        self.connect()
        try:
            self._sock.sendall((cmd + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise FshError("connexion fermée par le FSH4")
                buf += chunk
            return buf.decode(errors="replace").strip()
        except OSError as e:
            self.close()
            raise FshError(f"pas de réponse à {cmd} : {e}") from e
        except FshError:
            self.close()
            raise

    def _float(self, cmd: str) -> float:
        answer = self.query(cmd)
        try:
            return float(answer)
        except ValueError:
            self.close()
            raise FshError(f"réponse non numérique à {cmd} : {answer!r}") from None

    def read_settings(self) -> FshSettings:
        """Span, RBW, et fréquence des marqueurs allumés (un marqueur éteint répond quand même : on l'ignore)."""
        span, rbw = self._float("FREQ:SPAN?"), self._float("BAND?")
        active = [n for n in MARKER_NUMBERS if self.marker_on(n)]
        return FshSettings(span, rbw, tuple((n, self._float(f"CALC:MARK{n}:X?")) for n in active))

    def marker_on(self, n: int) -> bool:
        """Demander l'état ne rallume pas le marqueur (contrairement à :X? et :Y?)."""
        return self._float(f"CALC:MARK{n}?") == 1

    def read_level(self, n: int) -> float:
        return self._float(f"CALC:MARK{n}:Y?")


class FshReader:
    """Lit les marqueurs allumés du FSH4 (jusqu'à 6), en lecture seule."""

    def __init__(self, client: FshClient, settings_every_s: float = 10.0, clock=time.monotonic):
        self.client = client
        self.settings_every_s = settings_every_s
        self.clock = clock  # monotone : un changement d'heure du système ne bloque pas le relevé des réglages
        self._settings = None
        self._settings_at = None

    def read(self) -> FshReading:
        """Réglages et fréquences toutes les `settings_every_s` s ; niveaux des marqueurs allumés à chaque appel."""
        now = self.clock()
        if self._settings_at is None or now - self._settings_at >= self.settings_every_s:
            self._settings = self.client.read_settings()
            self._settings_at = now
        markers = {}
        for n, freq in self._settings.markers:
            # Sur le FSH4 V1.20, lire le niveau d'un marqueur éteint le rallume : on vérifie d'abord.
            if not self.client.marker_on(n):
                self._settings_at = None  # relevé complet au prochain cycle
                continue
            markers[n] = MarkerSample(freq, self.client.read_level(n))
        return FshReading(markers, self._settings)
