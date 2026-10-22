"""Porteuses : regroupement des IRD par fréquence et polarisation, rattachement des marqueurs FSH4.

Module sans état ni entrée-sortie : l'appartenance aux porteuses se recalcule à la demande à partir de
ce que chaque IRD reçoit (fréquence RF, polarisation, débit symbole) et des porteuses saisies à la main.
"""

from dataclasses import dataclass

from .config import CarrierEntry

DEFAULT_ROLL_OFF = 0.20
DEFAULT_TOLERANCE_KHZ = 1_000  # sans débit symbole connu : tolérance de regroupement et demi-bande occupée
IF_LIMIT_HZ = 3e9  # sous ce seuil, les marqueurs du FSH4 sont en fréquence intermédiaire


@dataclass(frozen=True)
class Tuning:
    rf_khz: int
    polarization: str | None
    symbol_rate: int | None
    roll_off: float | None
    measured: bool = True  # False : fréquence configurée d'un IRD décroché, pas mesurée


@dataclass(frozen=True)
class Carrier:
    key: str
    name: str
    rf_khz: int
    polarization: str | None
    symbol_rate: int | None
    roll_off: float | None
    irds: tuple
    manual: bool


@dataclass(frozen=True)
class MarkerMap:
    by_carrier: dict
    noise: tuple
    if_warning: bool
    ambiguous: tuple


def compatible_pol(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _pol_letter(pol: str | None) -> str:
    return {"vertical": "V", "horizontal": "H"}.get(pol, "?")


def carrier_key(rf_khz: int, pol: str | None) -> str:
    return f"{rf_khz // 100}-{_pol_letter(pol)}"


def default_name(rf_khz: int, pol: str | None) -> str:
    mhz = f"{rf_khz / 1000:,.1f}".replace(",", " ").replace(".", ",")
    return mhz if pol is None else f"{mhz} {_pol_letter(pol)}"


def _close(rf_a: int, sr_a: int | None, rf_b: int, sr_b: int | None) -> bool:
    rates = [sr for sr in (sr_a, sr_b) if sr]
    tolerance_khz = min(rates) / 2 / 1000 if rates else DEFAULT_TOLERANCE_KHZ
    return abs(rf_a - rf_b) < tolerance_khz


def _entry_tuning(e: CarrierEntry) -> tuple:
    """(fréquence en kHz, débit symbole en symboles/s ou None) d'une porteuse saisie."""
    return round(e.freq_mhz * 1000), round(e.symbol_rate_msps * 1e6) if e.symbol_rate_msps else None


def entry_matches(c: Carrier, e: CarrierEntry) -> bool:
    """Une porteuse saisie (ou un nom mémorisé) désigne-t-elle cette porteuse ?"""
    rf_khz, sr = _entry_tuning(e)
    return compatible_pol(c.polarization, e.polarization) and _close(c.rf_khz, c.symbol_rate, rf_khz, sr)


def _center(members: list) -> int:
    """Centre d'un groupe : médiane des fréquences mesurées ; celles configurées seulement en dernier recours."""
    freqs = sorted(t.rf_khz for t in members if t.measured) or sorted(t.rf_khz for t in members)
    return freqs[(len(freqs) - 1) // 2]  # médiane basse : déterministe avec deux IRD


def group_carriers(tunings: dict, entries: tuple, previous: tuple | list = ()) -> list:
    """Porteuses actuelles : IRD regroupés, porteuses manuelles ajoutées, noms mémorisés appliqués.
    `previous` (porteuses du calcul précédent) garde les clés stables quand une fréquence bouge un peu."""
    groups = []  # le premier IRD de chaque groupe donne la fréquence de comparaison
    for serial, t in tunings.items():
        for g in groups:
            if (compatible_pol(g["polarization"], t.polarization)
                    and _close(g["rf_khz"], g["symbol_rate"], t.rf_khz, t.symbol_rate)):
                g["irds"].append(serial)
                g["members"].append(t)
                g["polarization"] = g["polarization"] or t.polarization
                g["symbol_rate"] = g["symbol_rate"] or t.symbol_rate
                g["roll_off"] = g["roll_off"] or t.roll_off
                break
        else:
            groups.append(dict(rf_khz=t.rf_khz, polarization=t.polarization, symbol_rate=t.symbol_rate,
                               roll_off=t.roll_off, irds=[serial], members=[t]))

    carriers = [dict(rf_khz=_center(g["members"]), polarization=g["polarization"], symbol_rate=g["symbol_rate"],
                     roll_off=g["roll_off"], irds=tuple(g["irds"]), name=None, manual=False) for g in groups]
    for e in entries:
        rf_khz, sr = _entry_tuning(e)
        match = next((c for c in carriers if compatible_pol(c["polarization"], e.polarization)
                      and _close(c["rf_khz"], c["symbol_rate"], rf_khz, sr)), None)
        if match is not None:
            match["name"] = e.name or match["name"]
            match["manual"] = match["manual"] or e.manual
            match["symbol_rate"] = match["symbol_rate"] or sr
            match["polarization"] = match["polarization"] or e.polarization
        elif e.manual:
            carriers.append(dict(rf_khz=rf_khz, polarization=e.polarization, symbol_rate=sr, roll_off=None,
                                 irds=(), name=e.name, manual=True))

    used = set()
    for c in carriers:
        c["key"] = carrier_key(c["rf_khz"], c["polarization"])
        for p in previous:
            # Même polarisation exacte : la clé la contient, une polarisation devenue connue change donc de clé.
            if p.key not in used and p.polarization == c["polarization"] \
                    and _close(p.rf_khz, p.symbol_rate, c["rf_khz"], c["symbol_rate"]):
                c["key"] = p.key
                break
        used.add(c["key"])

    return sorted((Carrier(key=c["key"],
                           name=c["name"] or default_name(c["rf_khz"], c["polarization"]),
                           rf_khz=c["rf_khz"], polarization=c["polarization"], symbol_rate=c["symbol_rate"],
                           roll_off=c["roll_off"], irds=c["irds"], manual=c["manual"]) for c in carriers),
                  key=lambda c: c.rf_khz)


def band_hz(c: Carrier) -> tuple:
    """Bande occupée : centre ± débit symbole × (1 + roll-off) / 2."""
    center = c.rf_khz * 1000
    if not c.symbol_rate:
        half = DEFAULT_TOLERANCE_KHZ * 1000
    else:
        half = c.symbol_rate * (1 + (c.roll_off if c.roll_off is not None else DEFAULT_ROLL_OFF)) / 2
    return center - half, center + half


def attach_markers(carriers: list, marker_freqs: dict, fsh_pol: str | None, noise_markers: tuple = ()) -> MarkerMap:
    """Rattache chaque marqueur à la porteuse dont la bande le contient (polarisation compatible avec
    l'entrée du FSH4). Les marqueurs hors porteuse mesurent le bruit, sauf si `noise_markers` est donné."""
    if any(f < IF_LIMIT_HZ for f in marker_freqs.values()):
        return MarkerMap({}, (), True, ())
    bands = [(c, band_hz(c)) for c in carriers if compatible_pol(c.polarization, fsh_pol)]
    by_carrier, noise, ambiguous = {}, [], []
    for n in sorted(marker_freqs):
        f = marker_freqs[n]
        hits = [c for c, (low, high) in bands if low <= f <= high]
        if len(hits) == 1:
            by_carrier.setdefault(hits[0].key, []).append(n)
        elif len(hits) > 1:
            ambiguous.append(n)
        else:
            noise.append(n)
    if noise_markers:
        noise = [n for n in noise_markers if n in marker_freqs]
    return MarkerMap({k: tuple(v) for k, v in by_carrier.items()}, tuple(noise), False, tuple(ambiguous))


def center_marker(c: Carrier, mm: MarkerMap, marker_freqs: dict) -> int | None:
    """Marqueur de la porteuse le plus proche de son centre."""
    candidates = mm.by_carrier.get(c.key, ())
    if not candidates:
        return None
    return min(candidates, key=lambda n: abs(marker_freqs[n] - c.rf_khz * 1000))
