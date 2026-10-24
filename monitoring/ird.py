"""Client XML-RPC du DR5000 Ateme, en lecture seule : réception satellite, décodage (image, son, programme),
état matériel et journal de l'IRD."""

import http.client
import logging
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .collector import DeviceBusy
from .config import IrdConfig

REFRESH_BODY = b"xml=<refresh/>"
STATUS_BODY = b'xml=<status type="status"/>'
LOGS_BODY = b'xml=<logs type="logs"/>'
EXTRAS_TIMEOUT_S = 1.0  # état matériel et journal : requêtes annexes, courtes
ACTIVE_SAT = "presets/v1[@id='0']/channel/input/sat"  # configuration d'entrée satellite active
BER_SCALE = 1e8  # l'IRD donne le BER × 1e8 (son interface affiche ber / 1e8, et « < 1e-8 » pour 0)

log = logging.getLogger(__name__)


class IrdError(Exception):
    pass


class IrdBusy(IrdError, DeviceBusy):
    """HTTP 503 : l'IRD répond mais est occupé (observé pendant un reverrouillage)."""


@dataclass(frozen=True)
class AudioVoice:
    slot: int                # emplacement audio de l'IRD (0 à 15)
    pid: int | None
    codec: str | None        # ex. mpa, ac3, aac
    channels: int | None
    sample_rate: int | None  # Hz
    bitrate: int | None      # bit/s
    active: bool | None


@dataclass(frozen=True)
class IrdDetails:
    """Détail du décodage et configuration des sorties : affiché, pas enregistré."""
    video_codec: str | None = None
    video_profile: str | None = None
    video_level: str | None = None
    video_width: int | None = None
    video_height: int | None = None
    video_fps: float | None = None
    video_dar: str | None = None      # ex. 16_9
    video_pid: int | None = None
    audio: tuple = ()                 # AudioVoice des voies configurées
    sdi_formats: tuple = ()           # format de sortie des connecteurs SDI 1-2 et 3
    asi_descrambled: bool | None = None  # la sortie ASI renvoie le flux déchiffré
    ip_streams: int | None = None     # flux de sortie IP configurés
    genlock: bool | None = None
    loss_pattern: str | None = None   # mire envoyée sur les sorties quand l'image est perdue
    loss_freeze_ms: int | None = None  # image figée avant la mire


@dataclass(frozen=True)
class IrdSample:
    locked: bool
    ts_locked: bool
    cn_db: float | None
    margin_db: float | None
    ber: float | None
    ber_raw: str | None
    power_dbm: float | None
    if_khz: int | None
    symbol_rate: int | None
    modcod: str | None
    bitrate_kbps: int | None
    rf_khz: int | None = None        # fréquence RF reçue
    polarization: str | None = None  # "vertical" / "horizontal"
    roll_off: float | None = None
    rf_measured: bool | None = None  # True : IF mesurée + oscillateur ; False : fréquence configurée (décrochage)
    video_active: bool | None = None
    video_bitrate: int | None = None     # bit/s
    video_format: str | None = None      # ex. hd_1920x1080i_50
    audio_configured: int | None = None  # voies audio en mode « decode »
    audio_active: int | None = None      # voies configurées actives
    audio_bitrate: int | None = None     # bit/s, première voie active
    scrambled: bool | None = None
    descrambled: bool | None = None
    program_number: int | None = None
    service_name: str | None = None
    details: IrdDetails | None = None


@dataclass(frozen=True)
class IrdHealth:
    fpga_temp: float | None
    cpu_temp: float | None
    overheat: bool
    fan_failure: bool
    power_failure: bool


@dataclass(frozen=True)
class IrdLogMessage:
    n_id: int
    details: str
    level: str
    ended: bool


@dataclass(frozen=True)
class IrdReading:
    sample: IrdSample
    health: IrdHealth | None = None  # toutes les 10 s seulement
    logs: list | None = None         # idem


def band_state(s: IrdSample) -> int:
    """Code de la bande « État » : 3 lock perdu, 2 image / déchiffrement / flux, 1 BER ou son, 0 normal."""
    if not s.locked or not s.ts_locked:
        return 3
    if s.video_active is False or (s.scrambled and s.descrambled is False) or s.bitrate_kbps == 0:
        return 2
    if (s.ber or 0) > 0 or (s.audio_configured and s.audio_active == 0 and s.video_active):
        return 1
    return 0


def _bool(el, path):
    raw = _text(el, path)
    return None if raw is None else raw == "true"


def _text(el, path):
    node = el.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _num(el, path, conv, scale=1):
    raw = _text(el, path)
    if raw is None:
        return None
    try:
        return conv(raw) / scale if scale != 1 else conv(raw)
    except ValueError:
        return None


def _parse_details(root, prog, decoding=True) -> IrdDetails | None:
    out = root.find("presets/v1[@id='0']/channel/output")
    if prog is None and out is None:
        return None
    d = {}
    if prog is not None and decoding:
        dec = prog.find("video/decoded")
        if dec is not None:
            num, den = _num(dec, "fpsNum", int), _num(dec, "fpsDen", int)
            d.update(video_codec=_text(dec, "codec"), video_profile=_text(dec, "profile"), video_level=_text(dec, "level"),
                     video_width=_num(dec, "width", int), video_height=_num(dec, "height", int),
                     video_fps=num / den if num and den else None, video_dar=_text(dec, "dar"))
        d["video_pid"] = _num(prog, "video/configuration/pid", int)
        d["audio"] = tuple(
            AudioVoice(int(a.get("id", -1)), _num(a, "configuration/decoded/pid", int), _text(a, "status/decoded/codec"),
                       _num(a, "status/decoded/numChannels", int), _num(a, "status/decoded/sampleRate", int),
                       _num(a, "status/decoded/bitrate", int), _bool(a, "status/hasActivity"))
            for a in _configured_voices(prog))
    if prog is not None:
        d["sdi_formats"] = tuple(f for f in (_text(prog, "video/conn12Format"), _text(prog, "video/conn3Format")) if f)
    if out is not None:
        streams = out.findall("forward/ip/streams")
        d.update(asi_descrambled=_bool(out, "forward/asi/isDescrambled"),
                 ip_streams=sum(1 for st in streams if _text(st, "type") not in (None, "none")) if streams else None,
                 genlock=_bool(out, "genlock/isEnabled"), loss_pattern=_text(out, "emulation/pattern"),
                 loss_freeze_ms=_num(out, "emulation/freezeDuration", int))
    return IrdDetails(**d)


def _parse_xml(data: bytes):
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise IrdError(f"réponse XML invalide : {e}") from e


def _parse_ok(data: bytes):
    """Racine d'une réponse de l'IRD, qui doit être en status="ok"."""
    root = _parse_xml(data)
    if root.get("status") != "ok":
        raise IrdError(f"réponse en erreur : {_text(root, 'message') or root.get('status')}")
    return root


def _configured_voices(prog) -> list:
    """Voies audio du programme configurées pour être décodées (mode autre que « disable »)."""
    return [a for a in prog.findall("audio") if _text(a, "configuration/mode") not in (None, "disable")]


def parse_refresh(data: bytes) -> IrdSample:
    root = _parse_ok(data)
    inp = root.find("probe/input")
    if inp is None:
        raise IrdError("bloc probe/input absent")

    # Déverrouillé, l'IRD met puissance, débit symbole et IF à 0 et la modulation à « automatic unknown unknown » :
    # ce ne sont pas des mesures. C/N et marge à 0 restent (0 dB = décrochage, voulu sur les graphes).
    locked = _text(inp, "sat/locked") == "true"
    ts_locked = _text(inp, "tsLocked") == "true"
    parts = [_text(inp, "sat/mode"), _text(inp, "sat/modulation"), _text(inp, "sat/fec")]
    modcod = " ".join(parts).replace("_", "/") if locked and all(parts) and "unknown" not in parts else None

    # Fréquence RF : IF mesurée + oscillateur du LNB quand l'IRD est verrouillé ; sinon la fréquence
    # configurée (l'IF tombe à 0 pendant un décrochage).
    if_khz = _num(inp, "sat/frequency", int)
    lo_khz = _num(root, f"{ACTIVE_SAT}/oscillatorFrequency", int)
    downlink_khz = _num(root, f"{ACTIVE_SAT}/downlinkFrequency", int)
    if locked and if_khz and lo_khz:
        rf_khz, rf_measured = if_khz + lo_khz, True
    else:
        rf_khz = downlink_khz or None
        rf_measured = False if rf_khz else None
    roll_off_raw = _text(inp, "sat/rollOff") if locked else None
    try:
        roll_off = float(roll_off_raw.replace("_", ".")) if roll_off_raw else None
    except ValueError:
        roll_off = None

    # Décodage : image, son (voies configurées / actives), déchiffrement, programme sélectionné. Sans flux transport,
    # le décodeur garde ses dernières valeurs (voies « actives », débit vidéo) : elles ne sont pas reprises.
    prog = root.find("probe/decode/currentProgram")
    video = {}
    audio_configured = audio_active = audio_bitrate = None
    if prog is not None:
        video = dict(video_active=_bool(prog, "video/hasActivity"),
                     video_bitrate=_num(prog, "video/decoded/bitrate", int) if ts_locked else None,
                     video_format=_text(prog, "video/mainFormat"))
        voices = _configured_voices(prog)
        if prog.find("audio") is not None:
            audio_configured = len(voices)
            if ts_locked:
                active = [a for a in voices if _text(a, "status/hasActivity") == "true"]
                audio_active = len(active)
                audio_bitrate = _num(active[0], "status/decoded/bitrate", int) if active else None
    selected = next((p for p in root.findall("probe/tsDescriptor/program") if _text(p, "selected") == "true"), None)

    return IrdSample(
        locked=locked,
        ts_locked=ts_locked,
        cn_db=_num(inp, "sat/snr", float, 10),
        margin_db=_num(inp, "sat/snrMargin", float, 10),
        ber=_num(inp, "sat/ber", float, BER_SCALE) if locked else None,
        ber_raw=_text(inp, "sat/ber") if locked else None,
        power_dbm=_num(inp, "sat/power", float) if locked else None,
        if_khz=if_khz if locked else None,
        symbol_rate=(_num(inp, "sat/symbolRate", int) or None) if locked else None,
        modcod=modcod,
        bitrate_kbps=_num(inp, "bitrate", int),
        rf_khz=rf_khz,
        # Le réglage de polarisation ne compte que si l'IRD alimente lui-même le LNB (13 / 18 V) ; sinon le
        # signal arrive déjà polarisé (répartiteur, multiswitch…) et ce réglage ne dit rien.
        polarization=(_text(root, f"{ACTIVE_SAT}/lnb/polarization")
                      if _text(root, f"{ACTIVE_SAT}/lnb/isPowered") == "true" else None),
        roll_off=roll_off,
        rf_measured=rf_measured,
        **video,
        audio_configured=audio_configured,
        audio_active=audio_active,
        audio_bitrate=audio_bitrate,
        scrambled=_bool(prog, "scrambled") if prog is not None else None,
        descrambled=_bool(prog, "descrambled") if prog is not None else None,
        program_number=_num(selected, "programNumber", int) if selected is not None else None,
        service_name=_text(selected, "serviceName") if selected is not None else None,
        details=_parse_details(root, prog, decoding=ts_locked),
    )


def parse_health(data: bytes) -> IrdHealth:
    root = _parse_ok(data)
    return IrdHealth(fpga_temp=_num(root, "system/fpgaTemp", float), cpu_temp=_num(root, "system/cpuTemp", float),
                     overheat=_text(root, "system/overheat") == "true",
                     fan_failure=_text(root, "system/fanFailure") == "true",
                     power_failure=_text(root, "system/powerFailure") == "true")


def parse_logs(data: bytes) -> list:
    root = _parse_ok(data)
    out = []
    for m in root.findall("logs/message"):
        n_id = _num(m, "nId", int)
        if n_id is None:
            continue
        out.append(IrdLogMessage(n_id, _text(m, "cDetails") or "", _text(m, "level") or "information",
                                 _text(m, "nTimeEnd") not in (None, "-1")))
    return out


def parse_status(data: bytes) -> tuple[str, str]:
    """(numéro de série, nom) d'un IRD Ateme, d'après la réponse à <status/>."""
    root = _parse_xml(data)
    if _text(root, "system/vendorName") != "Ateme":
        raise IrdError("pas un IRD Ateme")
    serial = _text(root, "system/id")
    if not serial:
        raise IrdError("numéro de série absent")
    return serial, _text(root, "system/systemName") or ""


# Les IRD sont sur le réseau local : jamais par le proxy du système (PC d'entreprise sous Windows, où seuls les noms
# sans point le contournent).
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_url(request: urllib.request.Request, timeout: float):
    return _NO_PROXY.open(request, timeout=timeout)


def xml_request(url: str, body: bytes) -> urllib.request.Request:
    """Requête XML-RPC de l'interface web du DR5000 : corps brut en text/plain."""
    return urllib.request.Request(url, data=body, headers={"Content-Type": "text/plain; charset=utf-8"},
                                  method="POST")


class IrdClient:
    def __init__(self, host: str, cfg: IrdConfig):
        self.host = host
        self.cfg = cfg
        self.url = f"http://{host}/xml-rpc"

    def _post(self, body: bytes, timeout_s: float | None = None) -> bytes:
        try:
            with open_url(xml_request(self.url, body), timeout=timeout_s or self.cfg.timeout_s) as resp:
                if resp.status != 200:
                    raise IrdError(f"HTTP {resp.status}")
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 503:
                raise IrdBusy("IRD occupé (HTTP 503)") from e
            raise IrdError(f"IRD : HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise IrdError(f"IRD injoignable : {e}") from e
        except http.client.HTTPException as e:
            raise IrdError(f"réponse HTTP incomplète ou invalide ({type(e).__name__})") from e

    def fetch(self) -> IrdSample:
        return parse_refresh(self._post(REFRESH_BODY))

    def identify(self) -> tuple[str, str]:
        return parse_status(self._post(STATUS_BODY))

    def health(self) -> IrdHealth:
        return parse_health(self._post(STATUS_BODY, EXTRAS_TIMEOUT_S))

    def logs(self) -> list:
        return parse_logs(self._post(LOGS_BODY, EXTRAS_TIMEOUT_S))


class IrdReader:
    """Lit un IRD en vérifiant que c'est bien l'appareil attendu (numéro de série), à la première
    lecture et après chaque échec : une autre machine à la même adresse ne doit rien enregistrer."""

    def __init__(self, client: IrdClient, expected_serial: str, extras_every_s: float = 10.0, clock=time.monotonic):
        self.client = client
        self.expected_serial = expected_serial
        self.extras_every_s = extras_every_s
        self.clock = clock  # monotone : un changement d'heure du système ne bloque pas les relevés annexes
        self._verified = False
        self._extras_at = None
        self._extras_failing = set()  # requêtes annexes en échec (journalisées au premier échec et au retour)

    def _extra(self, name, fetch):
        # Requête annexe : son échec ne doit jamais faire échouer la lecture de l'IRD.
        try:
            value = fetch()
        except Exception as e:  # noqa: BLE001
            if name not in self._extras_failing:
                self._extras_failing.add(name)
                log.info("IRD %s : requête annexe « %s » en échec (%s)", self.client.host, name, e)
            return None
        if name in self._extras_failing:
            self._extras_failing.discard(name)
            log.info("IRD %s : requête annexe « %s » de nouveau en service", self.client.host, name)
        return value

    def read(self) -> IrdReading:
        try:
            if not self._verified:
                serial, _ = self.client.identify()
                if serial != self.expected_serial:
                    raise IrdError(f"autre appareil à cette adresse (n° {serial})")
                self._verified = True
            sample = self.client.fetch()
        except IrdBusy:
            raise
        except Exception:
            self._verified = False
            raise
        now = self.clock()
        if self._extras_at is not None and now - self._extras_at < self.extras_every_s:
            return IrdReading(sample, None, None)
        self._extras_at = now
        return IrdReading(sample, self._extra("état", self.client.health), self._extra("journal", self.client.logs))
