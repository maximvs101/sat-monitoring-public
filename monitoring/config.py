"""Chargement de config.toml en dataclasses gelées."""

import json
import re
import tomllib
from urllib.parse import urlsplit
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass(frozen=True)
class FshConfig:
    host: str = "192.168.20.222"
    port: int = 5555
    rate_hz: float = 4.0
    timeout_s: float = 2.0
    min_idle_s: float = 0.2  # silence minimal entre deux cycles, pour que le FSH4 rafraîchisse sa mesure
    polarization: str | None = None  # polarisation de l'entrée du FSH4 : "vertical", "horizontal" ou inconnue


@dataclass(frozen=True)
class IrdConfig:
    rate_hz: float = 1.0
    timeout_s: float = 3.0


@dataclass(frozen=True)
class IrdEntry:
    serial: str  # numéro de série de l'IRD ; vide tant qu'il n'a pas été identifié
    name: str
    host: str
    polarization: str | None = None  # imposée par l'opérateur ; None = automatique (réglage de l'IRD s'il alimente le LNB)


@dataclass(frozen=True)
class CarrierEntry:
    name: str
    freq_mhz: float
    polarization: str | None
    symbol_rate_msps: float | None
    manual: bool  # False : simple nom donné à une porteuse automatique


@dataclass(frozen=True)
class DetectConfig:
    ber_threshold: float = 1e-6
    ber_clear_s: float = 5.0
    margin_drop_db: float = 2.0
    margin_recover_db: float = 1.0
    margin_min_db: float = 0.0     # marge minimale (alerte « Marge basse ») ; 0 = désactivée
    marker_drop_db: float = 3.0
    marker_recover_db: float = 1.0
    median_window_s: float = 60.0
    min_history_s: float = 10.0
    drop_confirm_s: float = 3.0
    noise_markers: tuple = ()      # marqueurs FSH4 placés sur le plancher de bruit
    noise_rise_db: float = 3.0
    noise_recover_db: float = 1.0
    down_after_failures: int = 3
    retry_s: float = 5.0


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    live_window_min: int = 10
    event_padding_s: int = 30
    max_points: int = 5000


@dataclass(frozen=True)
class PathsConfig:
    db: str = "data/monitoring.db"
    log_dir: str = "logs"
    retention_days: int = 0  # 0 = tout garder


@dataclass(frozen=True)
class Config:
    fsh: FshConfig = field(default_factory=FshConfig)
    ird: IrdConfig = field(default_factory=IrdConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    irds: tuple = ()
    carriers: tuple = ()


POLARIZATIONS = ("vertical", "horizontal")
SECTIONS = ("fsh", "ird", "detect", "server", "paths")


def _check(cls, where, values):
    """Clés inconnues et polarisation invalide refusées ; `where` : « [fsh] », « [[irds]] »…"""
    unknown = set(values) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"clé inconnue dans {where} : {', '.join(sorted(unknown))}")
    if values.get("polarization") not in (None, *POLARIZATIONS):
        raise ValueError(f"polarisation inconnue dans {where} : {values['polarization']!r}")


def _section(cls, name, values):
    _check(cls, f"[{name}]", values)
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in values.items()}
    if "noise_markers" in values:
        nm = values["noise_markers"]
        if not isinstance(nm, tuple) or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 6 for n in nm):
            raise ValueError("noise_markers doit être une liste de numéros de marqueur entre 1 et 6")
    return cls(**values)


def _entries(cls, name, items):
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError(f"[[{name}]] doit être une liste de tables")
    out = []
    for item in items:
        _check(cls, f"[[{name}]]", item)
        out.append({f.name: item.get(f.name) for f in fields(cls)})
    return out


def _read_text(path: Path) -> str:
    """Contenu de config.toml, sans le BOM qu'ajoute le Bloc-notes de Windows (« UTF-8 avec BOM »)."""
    return path.read_text(encoding="utf-8").removeprefix("\ufeff")


def load_config(path: str | Path) -> Config:
    data = tomllib.loads(_read_text(Path(path)))
    defaults = {f.name: f.default_factory for f in fields(Config) if f.name in SECTIONS}
    unknown = set(data) - set(defaults) - {"irds", "carriers"}
    if unknown:
        raise ValueError(f"section inconnue : {', '.join(sorted(unknown))}")
    ird_section = dict(data.get("ird", {}))
    legacy_url = ird_section.pop("url", None)  # ancien format : un seul IRD
    sections = {name: _section(cls, name, ird_section if name == "ird" else data.get(name, {}))
                for name, cls in defaults.items()}
    irds = tuple(IrdEntry(serial=v["serial"] or "", name=v["name"] or "IRD", host=v["host"] or "",
                          polarization=v["polarization"])
                 for v in _entries(IrdEntry, "irds", data.get("irds", [])))
    if not irds and legacy_url:
        irds = (IrdEntry("", "IRD", urlsplit(legacy_url).hostname or ""),)
    carriers = tuple(CarrierEntry(name=v["name"] or "", freq_mhz=float(v["freq_mhz"]), polarization=v["polarization"],
                                  symbol_rate_msps=v["symbol_rate_msps"], manual=bool(v["manual"]))
                     for v in _entries(CarrierEntry, "carriers", data.get("carriers", [])))
    return Config(**sections, irds=irds, carriers=carriers)


def _header(line: str) -> str:
    """En-tête de section sans son éventuel commentaire."""
    return line.split("#", 1)[0].strip()


def _section_bounds(lines: list, section: str):
    """(ligne de l'en-tête [section], ligne de l'en-tête suivant), ou None si la section est absente."""
    start = next((i for i, l in enumerate(lines) if _header(l) == f"[{section}]"), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return start, end


def _set_key(text: str, section: str, key: str, value: str) -> str:
    """Remplace (ou ajoute) `key = value` dans [section] en gardant le reste du fichier, commentaires compris."""
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + f"\n[{section}]\n{key} = {value}\n"
    start, end = bounds
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(\"[^\"]*\"|[^\s#]+)(.*)$", re.S)
    for i in range(start + 1, end):
        m = pattern.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{value}{m.group(3)}"
            return "".join(lines)
    lines.insert(start + 1, f"{key} = {value}\n")
    return "".join(lines)


def _remove_key(text: str, section: str, key: str) -> str:
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, section)
    if bounds is None:
        return text
    start, end = bounds
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    return "".join(l for i, l in enumerate(lines) if not (start < i < end and pattern.match(l)))


def _replace_blocks(text: str, name: str, blocks: list[str]) -> str:
    """Retire tous les blocs [[name]] et ajoute les nouveaux en fin de fichier."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        header = _header(line)
        if header.startswith("["):
            skipping = header == f"[[{name}]]"
        if not skipping:
            out.append(line)
    text = "".join(out).rstrip("\n") + "\n"
    return text + "".join(f"\n[[{name}]]\n{b}" for b in blocks)


def _toml(value) -> str:
    if isinstance(value, str):
        # JSON n'échappe pas U+007F, interdit en TOML : un nom qui en contient rendrait le fichier illisible.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


def _block(pairs: dict) -> str:
    return "".join(f"{k} = {_toml(v)}\n" for k, v in pairs.items() if v is not None)


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_fsh(path: str | Path, host: str, port: int, polarization: str | None) -> None:
    """Enregistre l'adresse du FSH4 et la polarisation de son entrée."""
    path = Path(path)
    text = _read_text(path)
    text = _set_key(text, "fsh", "host", _toml(host))
    text = _set_key(text, "fsh", "port", str(int(port)))
    text = (_set_key(text, "fsh", "polarization", _toml(polarization)) if polarization
            else _remove_key(text, "fsh", "polarization"))
    _write(path, text)


def save_detect(path: str | Path, margin_drop_db: float, margin_min_db: float) -> None:
    """Enregistre les seuils de marge réglés depuis la page."""
    path = Path(path)
    text = _read_text(path)
    text = _set_key(text, "detect", "margin_drop_db", repr(float(margin_drop_db)))
    text = _set_key(text, "detect", "margin_min_db", repr(float(margin_min_db)))
    _write(path, text)


def save_irds(path: str | Path, irds: tuple) -> None:
    """Réécrit la liste [[irds]] (et retire l'ancienne clé url de [ird])."""
    path = Path(path)
    text = _remove_key(_read_text(path), "ird", "url")
    blocks = [_block({"serial": e.serial, "name": e.name, "host": e.host, "polarization": e.polarization})
              for e in irds]
    _write(path, _replace_blocks(text, "irds", blocks))


def save_carriers(path: str | Path, carriers: tuple) -> None:
    """Réécrit la liste [[carriers]] : porteuses manuelles et noms des porteuses automatiques."""
    path = Path(path)
    blocks = [_block({"name": c.name, "freq_mhz": float(c.freq_mhz), "polarization": c.polarization,
                      "symbol_rate_msps": None if c.symbol_rate_msps is None else float(c.symbol_rate_msps),
                      "manual": c.manual}) for c in carriers]
    _write(path, _replace_blocks(_read_text(path), "carriers", blocks))
