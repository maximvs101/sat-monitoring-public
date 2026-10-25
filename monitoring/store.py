"""Stockage SQLite des mesures, des états d'appareils et des événements."""

import math
import queue
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from .fsh import MARKER_NUMBERS, MarkerSample
from .ird import IrdHealth, IrdSample, band_state

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsh_marker (ts INTEGER NOT NULL, freq_hz REAL, level_dbm REAL, n INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS fsh_marker_ts ON fsh_marker(ts);
CREATE TABLE IF NOT EXISTS ird_sat (
    ts INTEGER NOT NULL, locked INTEGER, ts_locked INTEGER, cn_db REAL, margin_db REAL,
    ber REAL, ber_raw TEXT, power_dbm REAL, if_khz INTEGER, symbol_rate INTEGER,
    modcod TEXT, bitrate_kbps INTEGER, ird TEXT, rf_khz INTEGER, polarization TEXT, roll_off REAL);
CREATE TABLE IF NOT EXISTS ird_health (ts INTEGER NOT NULL, ird TEXT, fpga_temp REAL, cpu_temp REAL,
    overheat INTEGER, fan_failure INTEGER, power_failure INTEGER);
CREATE INDEX IF NOT EXISTS ird_health_ird_ts ON ird_health(ird, ts);
CREATE INDEX IF NOT EXISTS ird_health_ts ON ird_health(ts);
CREATE INDEX IF NOT EXISTS ird_sat_ts ON ird_sat(ts);
CREATE TABLE IF NOT EXISTS ird_names (serial TEXT PRIMARY KEY, name TEXT, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS device_status (ts INTEGER NOT NULL, device TEXT, state TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS device_status_ts ON device_status(ts);
CREATE INDEX IF NOT EXISTS device_status_device_ts ON device_status(device, ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts_start INTEGER NOT NULL, ts_end INTEGER,
    kind TEXT, severity TEXT, detail TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS events_ts_start ON events(ts_start);
CREATE INDEX IF NOT EXISTS events_ts_end ON events(ts_end);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
"""

# Colonnes numériques exposées par series() ; liste blanche contre l'injection SQL.
SERIES_COLUMNS = {
    "fsh_marker": {"freq_hz", "level_dbm"},
    "ird_sat": {"locked", "ts_locked", "cn_db", "margin_db", "ber", "power_dbm", "if_khz", "bitrate_kbps", "state"},
}
MARKER_STALE_MS = 30_000
READERS = 4  # connexions de lecture simultanées (requêtes de la page)
PURGE_BATCH = 50_000  # lignes supprimées par lot
LAST_LOCK_MS = 30 * 86_400_000  # recherche du débit symbole du dernier lock
STATE_GAP_MS = 3000  # au-delà, le trou entre deux mesures d'un IRD est une coupure (rien n'est compté)
# Une perte de lock ne doit pas disparaître dans une moyenne.
MIN_AGGREGATED = {"locked", "ts_locked"}
# Bande « État » : chaque intervalle agrégé prend la pire couleur de ses mesures.
MAX_AGGREGATED = {"state"}
# Colonnes ajoutées pour l'état des IRD (spec du 22/10/2026) : nom → type SQL.
DECODE_COLUMNS = {"video_active": "INTEGER", "video_bitrate": "INTEGER", "video_format": "TEXT",
                  "audio_configured": "INTEGER", "audio_active": "INTEGER", "audio_bitrate": "INTEGER",
                  "scrambled": "INTEGER", "descrambled": "INTEGER", "program_number": "INTEGER",
                  "service_name": "TEXT", "state": "INTEGER"}
BOOL_COLUMNS = ("locked", "ts_locked", "video_active", "scrambled", "descrambled")
HEALTH_FAULTS = ["overheat", "fan_failure", "power_failure"]  # booléens
HEALTH_COLUMNS = ["fpga_temp", "cpu_temp", *HEALTH_FAULTS]

IRD_COLUMNS = ["locked", "ts_locked", "cn_db", "margin_db", "ber", "ber_raw", "power_dbm",
               "if_khz", "symbol_rate", "modcod", "bitrate_kbps", "rf_khz", "polarization", "roll_off",
               *[c for c in DECODE_COLUMNS if c != "state"]]
# Colonnes lues à travers une expression : la puissance d'un IRD déverrouillé valait 0 dBm (valeur de l'IRD, pas
# une mesure) dans les lectures enregistrées avant le 22/10/2026 au soir.
SERIES_EXPRESSIONS = {("ird_sat", "power_dbm"): "CASE WHEN locked = 0 THEN NULL ELSE power_dbm END"}
IRD_SERIES_COLUMNS = ["locked", "ts_locked", "cn_db", "margin_db", "ber", "power_dbm", "state"]
TUNING_COLUMNS = ["rf_khz", "polarization", "symbol_rate", "roll_off"]
EVENT_COLUMNS = ["id", "ts_start", "ts_end", "kind", "severity", "detail", "source"]

class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")  # en WAL : sûr, et une seule écriture disque par point de reprise
        self._db.executescript(SCHEMA)
        self._migrate()
        # Lectures : quelques connexions partagées. En WAL, une requête d'historique longue n'en occupe qu'une : elle
        # ne bloque ni la collecte ni les autres lectures (état des appareils demandé en même temps par la page).
        self._readers = [sqlite3.connect(path, check_same_thread=False, isolation_level=None) for _ in range(READERS)]
        self._pool = queue.Queue()
        for conn in self._readers:
            self._pool.put(conn)

    def _columns(self, table):
        return {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        """Bases créées par une version précédente : colonnes et index ajoutés (rien n'est perdu)."""
        db = self._db
        # Avant les marqueurs multiples : tout est le marqueur 1.
        if "n" not in self._columns("fsh_marker"):
            db.execute("ALTER TABLE fsh_marker ADD COLUMN n INTEGER NOT NULL DEFAULT 1")
        # Index couvrants : l'historique se lit dans l'index, sans revenir à la table (dizaines de millions de lignes).
        db.execute("CREATE INDEX IF NOT EXISTS fsh_marker_n_ts_cov ON fsh_marker(n, ts, level_dbm, freq_hz)")
        db.execute("DROP INDEX IF EXISTS fsh_marker_n_ts")
        # Avant les IRD multiples (21/10/2026), puis l'état des IRD (22/10/2026).
        columns = self._columns("ird_sat")
        added = set()
        for col, kind in (("ird", "TEXT"), ("rf_khz", "INTEGER"), ("polarization", "TEXT"), ("roll_off", "REAL"),
                          *DECODE_COLUMNS.items()):
            if col not in columns:
                db.execute(f"ALTER TABLE ird_sat ADD COLUMN {col} {kind}")
                added.add(col)
        if "source" not in self._columns("events"):
            db.execute("ALTER TABLE events ADD COLUMN source TEXT")
        db.execute(f"CREATE INDEX IF NOT EXISTS ird_sat_ird_ts_cov ON ird_sat(ird, ts, "
                   f"{', '.join(IRD_SERIES_COLUMNS + TUNING_COLUMNS)})")
        db.execute("DROP INDEX IF EXISTS ird_sat_ird_ts")
        if "state" in added:
            # Mesures d'avant l'état des IRD : couleur de la bande d'après le lock et le BER seulement.
            db.execute("UPDATE ird_sat SET state = CASE WHEN locked = 0 OR ts_locked = 0 THEN 3 "
                       "WHEN ber > 0 THEN 1 ELSE 0 END")

    def _exec(self, sql, params=()):
        with self._lock:
            return self._db.execute(sql, params)

    def _all(self, sql, params=()):
        conn = self._pool.get()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            self._pool.put(conn)

    def add_marker(self, ts: int, s: MarkerSample, n: int = 1) -> None:
        self._exec("INSERT INTO fsh_marker (ts, freq_hz, level_dbm, n) VALUES (?, ?, ?, ?)",
                   (ts, s.freq_hz, s.level_dbm, n))

    def add_markers(self, ts: int, markers: dict) -> None:
        """Tous les marqueurs d'une lecture du FSH4 en une seule écriture : {numéro: MarkerSample}."""
        if markers:
            rows = [v for n, m in markers.items() for v in (ts, m.freq_hz, m.level_dbm, n)]
            self._exec("INSERT INTO fsh_marker (ts, freq_hz, level_dbm, n) VALUES "
                       + ", ".join(["(?, ?, ?, ?)"] * len(markers)), rows)

    def add_ird(self, ts: int, serial: str, s: IrdSample) -> None:
        d = asdict(s)
        for c in BOOL_COLUMNS:
            d[c] = None if d[c] is None else int(d[c])
        for c, v in d.items():  # dernier rempart : un entier trop grand pour SQLite ne fait pas perdre la lecture
            if isinstance(v, int) and not isinstance(v, bool) and not -2**63 <= v < 2**63:
                d[c] = None
        self._exec(f"INSERT INTO ird_sat (ts, ird, state, {', '.join(IRD_COLUMNS)}) "
                   f"VALUES (?, ?, ?, {', '.join('?' * len(IRD_COLUMNS))})",
                   (ts, serial, band_state(s), *(d[c] for c in IRD_COLUMNS)))

    def add_health(self, ts: int, serial: str, h: IrdHealth) -> None:
        self._exec("INSERT INTO ird_health VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (ts, serial, h.fpga_temp, h.cpu_temp, int(h.overheat), int(h.fan_failure), int(h.power_failure)))

    def set_ird_name(self, serial: str, name: str, ts: int) -> None:
        self._exec("INSERT INTO ird_names VALUES (?, ?, ?) ON CONFLICT(serial) "
                   "DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen", (serial, name, ts))

    def ird_names(self) -> dict:
        return dict(self._all("SELECT serial, name FROM ird_names"))

    def add_status(self, ts: int, device: str, state: str, detail: str) -> None:
        self._exec("INSERT INTO device_status VALUES (?, ?, ?, ?)", (ts, device, state, detail))

    def open_event(self, ts: int, kind: str, severity: str, detail: str, source: str | None = None) -> int:
        return self._exec("INSERT INTO events (ts_start, kind, severity, detail, source) VALUES (?, ?, ?, ?, ?)",
                          (ts, kind, severity, detail, source)).lastrowid

    def close_event(self, event_id: int, ts_end: int, detail: str | None = None) -> None:
        """Ferme l'événement ; `detail` remplace son détail (motif de fermeture ajouté)."""
        self._exec("UPDATE events SET ts_end = ?, detail = COALESCE(?, detail) WHERE id = ?", (ts_end, detail, event_id))

    def set_severity(self, event_id: int, severity: str) -> None:
        self._exec("UPDATE events SET severity = ? WHERE id = ?", (severity, event_id))

    def close_open_events(self, ts_end: int) -> int:
        # Le motif dit que le défaut n'a pas forcément cessé : la page ne refait pas sonner une alerte acquittée qui
        # se rouvre au redémarrage.
        return self._exec("UPDATE events SET ts_end = MAX(ts_start, ?), detail = detail || ' — service arrêté' "
                          "WHERE ts_end IS NULL", (ts_end,)).rowcount

    def event(self, event_id: int) -> dict:
        # Connexion d'écriture : appelée par la détection (fils de collecte), elle ne doit jamais attendre
        # une requête d'historique en cours sur la connexion de lecture.
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(zip(EVENT_COLUMNS, row))

    def purge_before(self, ts: int) -> None:
        """Supprime les mesures, états et événements terminés antérieurs à ts. Le dernier état de chaque appareil
        est conservé. Par lots : la collecte n'attend jamais plus qu'un lot, même pour des millions de lignes."""
        for table in ("fsh_marker", "ird_sat", "ird_health"):
            self._delete_in_batches(f"DELETE FROM {table} WHERE rowid IN "
                                    f"(SELECT rowid FROM {table} WHERE ts < ? LIMIT {PURGE_BATCH})", (ts,))
        self._delete_in_batches(
            "DELETE FROM device_status WHERE rowid IN (SELECT rowid FROM device_status WHERE ts < ? AND rowid NOT IN "
            f"(SELECT MAX(rowid) FROM device_status GROUP BY device) LIMIT {PURGE_BATCH})", (ts,))
        self._delete_in_batches(f"DELETE FROM events WHERE id IN (SELECT id FROM events WHERE ts_end IS NOT NULL "
                                f"AND ts_end < ? LIMIT {PURGE_BATCH})", (ts,))
        with self._lock:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # le journal ne garde pas la taille de la purge

    def _delete_in_batches(self, sql: str, params: tuple) -> None:
        while True:
            with self._lock:
                deleted = self._db.execute(sql, params).rowcount
            if deleted < PURGE_BATCH:
                return

    def last_event_start(self, kind: str) -> int | None:
        return self._all("SELECT MAX(ts_start) FROM events WHERE kind = ?", (kind,))[0][0]

    def set_alive(self, ts: int) -> None:
        """Le service tournait encore à ts (marque régulière) : même sans aucune mesure, une coupure d'appareil
        en cours à l'arrêt brutal du service dure au moins jusque-là."""
        self._exec("INSERT INTO meta (key, value) VALUES ('alive', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)", (ts,))

    def last_ts(self) -> int | None:
        """Dernière heure où le service a vu quelque chose : mesure ou marque de vie."""
        return self._all("SELECT MAX(m) FROM (SELECT MAX(ts) m FROM fsh_marker UNION ALL SELECT MAX(ts) FROM ird_sat "
                         "UNION ALL SELECT value FROM meta WHERE key = 'alive')")[0][0]

    def marker_series(self, from_ts: int, to_ts: int, max_points: int) -> dict:
        """Une série par marqueur FSH4 présent dans la plage : {numéro: série}."""
        # Un marqueur est présent s'il a au moins une mesure dans la plage (index (n, ts) : sans parcourir la plage).
        numbers = [n for n in MARKER_NUMBERS if self._all(
            "SELECT 1 FROM fsh_marker WHERE n = ? AND ts >= ? AND ts <= ? LIMIT 1", (n, from_ts, to_ts))]
        return {n: self.series("fsh_marker", ["freq_hz", "level_dbm"], from_ts, to_ts, max_points, n=n)
                for n in numbers}

    def ird_series(self, from_ts: int, to_ts: int, max_points: int) -> dict:
        """Une série par IRD présent dans la plage : {numéro de série: série + « tuning »}."""
        serials = [serial for serial in self._serials() if self._all(
            "SELECT 1 FROM ird_sat WHERE ird = ? AND ts >= ? AND ts <= ? LIMIT 1", (serial, from_ts, to_ts))]
        out = {}
        for serial in serials:
            s = self.series("ird_sat", IRD_SERIES_COLUMNS, from_ts, to_ts, max_points, ird=serial)
            row = self._all(f"SELECT {', '.join(TUNING_COLUMNS)} FROM ird_sat WHERE ird = ? AND ts >= ? AND ts <= ? "
                            "AND rf_khz IS NOT NULL ORDER BY ts DESC, rowid DESC LIMIT 1", (serial, from_ts, to_ts))
            s["tuning"] = dict(zip(TUNING_COLUMNS, row[0])) if row else None
            if s["tuning"] and not s["tuning"]["symbol_rate"]:
                # Déverrouillé en fin de période : débit symbole et roll-off du dernier lock sur cette fréquence.
                # Même porteuse : à moins d'un demi-débit symbole (fréquence mesurée contre fréquence configurée).
                # Dans le mois qui précède seulement : sans lock récent, inconnu (et pas de parcours de tout l'historique).
                last = self._all("SELECT symbol_rate, roll_off FROM ird_sat WHERE ird = ? AND ts <= ? AND ts >= ? "
                                 "AND locked = 1 AND symbol_rate > 0 AND ABS(rf_khz - ?) * 2000 < symbol_rate "
                                 "ORDER BY ts DESC LIMIT 1", (serial, to_ts, to_ts - LAST_LOCK_MS, s["tuning"]["rf_khz"]))
                s["tuning"]["symbol_rate"], s["tuning"]["roll_off"] = last[0] if last else (None, None)
            s["segments"] = self._segments(serial, from_ts, to_ts)
            out[serial] = s
        return out

    def _state_durations(self, where: str, params: tuple, from_ts: int, bucket: int, buckets: list) -> list:
        """Temps passé dans chaque code de la bande « État » (0 à 3), en ms, par intervalle agrégé : chaque mesure
        compte jusqu'à la suivante, sauf au-delà de STATE_GAP_MS (coupure). Un défaut d'une seconde ne compte pas
        pour tout l'intervalle, comme le ferait le pire code de l'intervalle."""
        rows = self._all(
            "SELECT (ts - ?) / ? AS b, "
            + ", ".join(f"SUM(CASE WHEN state = {k} THEN dt ELSE 0 END)" for k in range(4))
            + " FROM (SELECT ts, state, CASE WHEN nxt - ts <= ? THEN nxt - ts ELSE 0 END AS dt FROM ("
            f"  SELECT ts, state, LEAD(ts) OVER (ORDER BY ts) AS nxt FROM ird_sat WHERE {where})) GROUP BY b",
            (from_ts, bucket, STATE_GAP_MS, *params))
        by_bucket = {r[0]: [int(v or 0) for v in r[1:]] for r in rows}
        return [by_bucket.get(b, [0, 0, 0, 0]) for b in buckets]

    def _serials(self) -> list:
        """Numéros de série présents dans la base, lus dans l'index (ird, ts) sans parcourir la table."""
        return [r[0] for r in self._all(
            "WITH RECURSIVE s(ird) AS (SELECT MIN(ird) FROM ird_sat WHERE ird IS NOT NULL UNION ALL "
            "SELECT (SELECT MIN(ird) FROM ird_sat WHERE ird > s.ird) FROM s WHERE s.ird IS NOT NULL) "
            "SELECT ird FROM s WHERE ird IS NOT NULL")]

    def _segments(self, serial: str, from_ts: int, to_ts: int) -> list:
        """Périodes pendant lesquelles l'IRD est resté sur un même réglage (fréquence au MHz près, polarisation)."""
        changes = self._all(
            f"SELECT ts, {', '.join(TUNING_COLUMNS)} FROM ("
            f"  SELECT ts, {', '.join(TUNING_COLUMNS)}, LAG(rf_khz / 1000) OVER w AS prev_mhz,"
            "          LAG(polarization) OVER w AS prev_pol, ROW_NUMBER() OVER w AS rn"
            "  FROM ird_sat WHERE ird = ? AND ts >= ? AND ts <= ? AND rf_khz IS NOT NULL"
            "  WINDOW w AS (ORDER BY ts, rowid))"
            " WHERE rn = 1 OR rf_khz / 1000 != prev_mhz OR polarization IS NOT prev_pol ORDER BY ts",
            (serial, from_ts, to_ts))
        segments = []
        for ts, rf, pol, sr, roll in changes:
            last = segments[-1] if segments else None
            # Un passage de frontière de MHz sans vrai changement de réglage ne coupe pas la période.
            if last and last["polarization"] == pol and abs(last["rf_khz"] - rf) < (sr or 2_000_000) / 2 / 1000:
                continue
            segments.append({"from": ts, "to": None, "rf_khz": rf, "polarization": pol,
                             "symbol_rate": sr, "roll_off": roll})
        if segments:
            for a, b in zip(segments, segments[1:]):
                a["to"] = b["from"] - 1
            segments[-1]["to"] = self._all("SELECT MAX(ts) FROM ird_sat WHERE ird = ? AND ts >= ? AND ts <= ?",
                                           (serial, from_ts, to_ts))[0][0]
        return segments

    def series(self, table: str, columns: list[str], from_ts: int, to_ts: int, max_points: int,
               n: int | None = None, ird: str | None = None) -> dict:
        allowed = SERIES_COLUMNS.get(table)
        if allowed is None or not columns or not set(columns) <= allowed:
            raise ValueError(f"série inconnue : {table} {columns}")
        where, params = "ts >= ? AND ts <= ?", (from_ts, to_ts)
        if n is not None:
            where, params = where + " AND n = ?", params + (int(n),)
        if ird is not None:
            where, params = where + " AND ird = ?", params + (ird,)
        count = self._all(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)[0][0]
        expr = {c: SERIES_EXPRESSIONS.get((table, c), c) for c in columns}
        if count <= max_points:
            rows = self._all(f"SELECT ts, {', '.join(expr[c] for c in columns)} FROM {table} WHERE {where} ORDER BY ts", params)
            out = {"aggregated": False, "ts": [r[0] for r in rows]}
            for i, c in enumerate(columns, 1):
                out[c] = [r[i] for r in rows]
            return out

        bucket = max(1, math.ceil((to_ts - from_ts) / max_points))
        exprs = []
        for c in columns:
            e = expr[c]
            main = f"MIN({e})" if c in MIN_AGGREGATED else f"MAX({e})" if c in MAX_AGGREGATED else f"AVG({e})"
            exprs += [main, f"MIN({e})", f"MAX({e})"]
        rows = self._all(
            f"SELECT (ts - ?) / ? AS b, {', '.join(exprs)} FROM {table} WHERE {where} GROUP BY b ORDER BY b",
            (from_ts, bucket, *params))
        out = {"aggregated": True, "ts": [from_ts + r[0] * bucket for r in rows]}
        for i, c in enumerate(columns):
            out[c] = [r[1 + 3 * i] for r in rows]
            out[c + "_min"] = [r[2 + 3 * i] for r in rows]
            out[c + "_max"] = [r[3 + 3 * i] for r in rows]
        if table == "ird_sat" and ird is not None and "state" in columns:
            out["state_ms"] = self._state_durations(where, params, from_ts, bucket, [r[0] for r in rows])
        return out

    def events(self, from_ts: int, to_ts: int) -> list[dict]:
        rows = self._all(
            f"SELECT {', '.join(EVENT_COLUMNS)} FROM events "
            "WHERE ts_start <= ? AND (ts_end IS NULL OR ts_end >= ?) ORDER BY ts_start DESC, id DESC",
            (to_ts, from_ts))
        return [dict(zip(EVENT_COLUMNS, r)) for r in rows]

    def latest(self) -> dict:
        # Dernière valeur de chaque marqueur (une recherche par l'index (n, ts) chacun : jamais toute la table).
        last = {}
        for n in MARKER_NUMBERS:
            row = self._all("SELECT ts, freq_hz, level_dbm FROM fsh_marker WHERE n = ? ORDER BY ts DESC LIMIT 1", (n,))
            if row:
                last[n] = dict(zip(["ts", "freq_hz", "level_dbm"], row[0]))
        last_ts = max((m["ts"] for m in last.values()), default=0)
        # Sauf ceux éteints depuis plus de 30 s.
        markers = {n: m for n, m in last.items() if m["ts"] >= last_ts - MARKER_STALE_MS}
        # Marqueur principal : le 1, sinon le plus récent.
        marker = last.get(1) or max(last.values(), key=lambda m: m["ts"], default=None)
        irds = {}
        # IRD distincts en sautant d'une valeur à la suivante dans l'index (ird, ts), sans tout parcourir.
        for (serial,) in self._all(
                "WITH RECURSIVE s(ird) AS (SELECT MIN(ird) FROM ird_sat WHERE ird IS NOT NULL UNION ALL "
                "SELECT (SELECT MIN(ird) FROM ird_sat WHERE ird > s.ird) FROM s WHERE s.ird IS NOT NULL) "
                "SELECT ird FROM s WHERE ird IS NOT NULL"):
            row = self._all(f"SELECT ts, state, {', '.join(IRD_COLUMNS)} FROM ird_sat WHERE ird = ? "
                            "ORDER BY ts DESC, rowid DESC LIMIT 1", (serial,))[0]
            d = dict(zip(["ts", "state", *IRD_COLUMNS], row))
            for c in BOOL_COLUMNS:
                d[c] = None if d[c] is None else bool(d[c])
            health = self._all(f"SELECT ts, {', '.join(HEALTH_COLUMNS)} FROM ird_health WHERE ird = ? "
                               "ORDER BY ts DESC, rowid DESC LIMIT 1", (serial,))
            d["health"] = None
            if health:
                d["health"] = dict(zip(["ts", *HEALTH_COLUMNS], health[0]))
                for c in HEALTH_FAULTS:
                    d["health"][c] = bool(d["health"][c])
            irds[serial] = d
        devices = {}
        for device, state, ts, detail in self._all(
                "SELECT device, state, ts, detail FROM device_status d "
                "WHERE rowid = (SELECT rowid FROM device_status WHERE device = d.device ORDER BY ts DESC, rowid DESC LIMIT 1)"):
            devices[device] = {"state": state, "ts": ts, "detail": detail}
        return {
            "marker": marker,
            "markers": markers,
            "irds": irds,
            "devices": devices,
        }

    def close(self) -> None:
        for conn in self._readers:
            conn.close()
        with self._lock:
            self._db.close()
