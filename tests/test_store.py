import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from monitoring.fsh import MarkerSample
from monitoring.ird import IrdSample
from monitoring.store import Store


def ird(margin=10.5, locked=True, ber=0.0):
    return IrdSample(locked=locked, ts_locked=True, cn_db=15.7, margin_db=margin, ber=ber, ber_raw=str(ber), power_dbm=-38.0,
                     if_khz=1470806, symbol_rate=24760000, modcod="dvbs2 qpsk 5/6", bitrate_kbps=40969)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sub" / "m.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_roundtrip_raw_series(self):
        for i in range(3):
            self.store.add_marker(1000 + i, MarkerSample(11.2e9, -40.0 - i))
        s = self.store.series("fsh_marker", ["level_dbm"], 0, 5000, 5000)
        self.assertIs(s["aggregated"], False)
        self.assertEqual(s["ts"], [1000, 1001, 1002])
        self.assertEqual(s["level_dbm"], [-40.0, -41.0, -42.0])

    def test_aggregation(self):
        for i in range(10_000):
            self.store.add_ird(i * 1000, "A", ird(margin=10.0 + (i % 7) / 10))
        s = self.store.series("ird_sat", ["margin_db"], 0, 10_000_000, 100)
        self.assertIs(s["aggregated"], True)
        self.assertLessEqual(len(s["ts"]), 100)
        for lo, avg, hi in zip(s["margin_db_min"], s["margin_db"], s["margin_db_max"]):
            self.assertLessEqual(lo, avg)
            self.assertLessEqual(avg, hi)

    def test_markers_of_one_reading_written_together(self):
        # Une lecture du FSH4 (jusqu'à 6 marqueurs) : une seule écriture, pas une par marqueur.
        statements = []
        self.store._db.set_trace_callback(statements.append)
        self.store.add_markers(1000, {1: MarkerSample(11.2e9, -40.0), 4: MarkerSample(11.21e9, -67.0)})
        self.store._db.set_trace_callback(None)
        self.assertEqual(sum(1 for q in statements if q.startswith("INSERT")), 1)
        self.assertEqual(sorted(self.store.marker_series(0, 2000, 10)), [1, 4])

    def test_last_lock_lookup_is_bounded(self):
        # Débit symbole du dernier lock : cherché dans le mois qui précède, pas dans tout l'historique.
        tuned = dict(rf_khz=12_592_060, polarization="vertical")
        self.store.add_ird(1000, "A", replace(ird(), symbol_rate=7_200_000, roll_off=0.25, **tuned))
        late = 40 * 86_400_000
        self.store.add_ird(late, "A", replace(ird(locked=False), symbol_rate=0, roll_off=0.35, **tuned))
        tuning = self.store.ird_series(late - 1000, late + 1000, 100)["A"]["tuning"]
        self.assertIsNone(tuning["symbol_rate"])

    def test_aggregated_state_durations(self):
        # 30 jours ≈ intervalles de 29 min : une perte de lock de 2 s doit compter 2 s, pas tout l'intervalle.
        for i in range(600):
            self.store.add_ird(i * 1000, "A", ird(locked=i not in (100, 101)))
        s = self.store.series("ird_sat", ["state"], 0, 600_000, 2, ird="A")
        self.assertTrue(s["aggregated"])
        totals = [sum(b[k] for b in s["state_ms"]) for k in range(4)]
        self.assertEqual(totals[3], 2000)
        self.assertEqual(totals[0], 597_000)  # 599 intervalles d'1 s, dont 2 perdus
        self.assertEqual(len(s["state_ms"]), len(s["ts"]))

    def test_lock_aggregates_to_min(self):
        for i in range(200):
            self.store.add_ird(i * 1000, "A", ird(locked=(i != 50)))
        s = self.store.series("ird_sat", ["locked"], 0, 200_000, 10)
        self.assertIn(0, s["locked"])
        self.assertEqual(s["locked"].count(0), 1)

    def test_power_absent_when_unlocked(self):
        # Les anciennes lectures déverrouillées ont 0 dBm (valeur de l'IRD, pas une mesure) : pas de pic à 0 dBm.
        for i in range(10):
            self.store.add_ird(i * 1000, "A", replace(ird(locked=i < 5), power_dbm=0.0 if i >= 5 else -40.0))
        raw = self.store.series("ird_sat", ["power_dbm"], 0, 10_000, 100)
        self.assertEqual(raw["power_dbm"], [-40.0] * 5 + [None] * 5)
        agg = self.store.series("ird_sat", ["power_dbm"], 0, 10_000, 2)
        self.assertTrue(agg["aggregated"])
        self.assertEqual([v for v in agg["power_dbm"] if v is not None], [-40.0])

    def test_past_tuning_keeps_symbol_rate_of_last_lock(self):
        # Déverrouillé, l'IRD ne donne plus de débit symbole : la période passée garde celui du dernier lock.
        # Verrouillé : fréquence mesurée ; déverrouillé : fréquence configurée, quelques kHz plus loin.
        self.store.add_ird(1000, "A", replace(ird(), symbol_rate=7_200_000, roll_off=0.25, rf_khz=12_592_023))
        self.store.add_ird(1500, "A", replace(ird(), symbol_rate=27_500_000, roll_off=0.2, rf_khz=11_000_000))  # autre porteuse
        self.store.add_ird(1800, "A", replace(ird(), symbol_rate=7_200_000, roll_off=0.25, rf_khz=12_592_031))
        self.store.add_ird(2000, "A", replace(ird(locked=False), symbol_rate=0, roll_off=0.35, rf_khz=12_592_060))
        tuning = self.store.ird_series(0, 5000, 100)["A"]["tuning"]
        self.assertEqual((tuning["rf_khz"], tuning["symbol_rate"], tuning["roll_off"]), (12_592_060, 7_200_000, 0.25))

    def test_series_rejects_unknown_column(self):
        with self.assertRaises(ValueError):
            self.store.series("fsh_marker", ["level_dbm; DROP"], 0, 1, 10)
        with self.assertRaises(ValueError):
            self.store.series("events", ["kind"], 0, 1, 10)

    def test_events_overlap(self):
        a = self.store.open_event(100, "margin_drop", "warning", "x")
        self.store.close_event(a, 200)
        self.store.open_event(100, "lock_lost", "critical", "y")
        self.assertEqual({e["id"] for e in self.store.events(150, 300)}, {a, a + 1})
        kinds = [e["kind"] for e in self.store.events(250, 300)]
        self.assertEqual(kinds, ["lock_lost"])
        self.assertEqual([e["kind"] for e in self.store.events(500, 600)], ["lock_lost"])

    def test_close_open_events_on_restart(self):
        self.store.add_ird(5000, "A", ird())
        self.store.open_event(4000, "lock_lost", "critical", "")
        self.store.close()
        self.store = Store(self.path)
        last = self.store.last_ts()
        self.assertEqual(last, 5000)
        self.assertEqual(self.store.close_open_events(last), 1)
        self.assertEqual(self.store.events(0, 10_000)[0]["ts_end"], 5000)

    def test_close_open_events_never_before_start(self):
        self.store.add_ird(1000, "A", ird())
        self.store.open_event(5000, "device_down", "critical", "ird")
        self.store.close_open_events(self.store.last_ts())
        ev = self.store.events(0, 10_000)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["ts_end"], 5000)

    def test_reads_do_not_block_writes(self):
        import threading
        held = self.store._pool.get()  # une lecture longue en cours
        try:
            t = threading.Thread(target=self.store.add_marker, args=(1, MarkerSample(11.2e9, -40.0)))
            t.start()
            t.join(0.5)
            self.assertFalse(t.is_alive(), "l'écriture attend la lecture")
        finally:
            self.store._pool.put(held)

    def test_purge_before(self):
        for ts in (1000, 9000):
            self.store.add_marker(ts, MarkerSample(11.2e9, -40.0))
            self.store.add_ird(ts, "A", ird())
            self.store.add_status(ts, "fsh", "up", "")
        old = self.store.open_event(1000, "lock_lost", "critical", "")
        self.store.close_event(old, 2000)
        self.store.open_event(1500, "device_down", "critical", "fsh")  # encore ouvert : gardé
        self.store.purge_before(5000)
        self.assertEqual(self.store.series("fsh_marker", ["level_dbm"], 0, 10_000, 10)["ts"], [9000])
        self.assertEqual(self.store.series("ird_sat", ["margin_db"], 0, 10_000, 10)["ts"], [9000])
        self.assertEqual([e["kind"] for e in self.store.events(0, 10_000)], ["device_down"])
        self.assertEqual(self.store.latest()["devices"]["fsh"]["ts"], 9000)

    def test_marker_series_per_number(self):
        for ts in (1000, 2000):
            self.store.add_marker(ts, MarkerSample(11.22e9, -50.0), n=1)
            self.store.add_marker(ts, MarkerSample(11.24e9, -60.0), n=4)
        s = self.store.marker_series(0, 5000, 100)
        self.assertEqual(sorted(s), [1, 4])
        self.assertEqual(s[4]["level_dbm"], [-60.0, -60.0])
        self.assertEqual(s[4]["freq_hz"], [11.24e9, 11.24e9])
        self.assertEqual(s[1]["ts"], [1000, 2000])

    def test_existing_database_is_migrated(self):
        import sqlite3
        self.store.close()
        path = Path(self.dir.name) / "ancienne.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE fsh_marker (ts INTEGER NOT NULL, freq_hz REAL, level_dbm REAL)")
        db.execute("INSERT INTO fsh_marker VALUES (1000, 11.22e9, -50.0)")
        db.commit()
        db.close()
        self.store = Store(path)
        self.assertEqual(self.store.marker_series(0, 5000, 100)[1]["level_dbm"], [-50.0])

    def test_latest_markers(self):
        self.store.add_marker(1, MarkerSample(11.22e9, -50.0), n=1)
        self.store.add_marker(1, MarkerSample(11.24e9, -60.0), n=4)
        self.store.add_marker(2, MarkerSample(11.22e9, -51.0), n=1)
        latest = self.store.latest()
        self.assertEqual(latest["marker"]["level_dbm"], -51.0)
        self.assertEqual({n: m["level_dbm"] for n, m in latest["markers"].items()}, {1: -51.0, 4: -60.0})

    def test_latest(self):
        self.assertEqual(self.store.latest(), {"marker": None, "markers": {}, "irds": {}, "devices": {}})
        self.store.add_marker(1, MarkerSample(11.2e9, -40.0))
        self.store.add_ird(2, "A", ird())
        self.store.add_status(3, "fsh", "down", "délai")
        self.store.add_status(4, "fsh", "up", "")
        latest = self.store.latest()
        self.assertEqual(latest["marker"]["level_dbm"], -40.0)
        self.assertEqual(latest["irds"]["A"]["margin_db"], 10.5)
        self.assertIs(latest["irds"]["A"]["locked"], True)
        self.assertEqual(latest["devices"]["fsh"], {"state": "up", "ts": 4, "detail": ""})


def tuned(rf_khz=12_583_023, pol="vertical", margin=6.3):
    return replace(ird(margin=margin), rf_khz=rf_khz, polarization=pol, symbol_rate=7_200_000, roll_off=0.25)


class ContentionTest(unittest.TestCase):
    """La collecte (alertes) ne doit jamais attendre une requête d'historique ; latest() passe par les index."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "m.db")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_event_readback_not_blocked_by_history_query(self):
        import threading
        event_id = self.store.open_event(1000, "lock_lost", "critical", "x", "ird:A")
        done = []
        held = [self.store._pool.get() for _ in self.store._readers]  # toutes les lectures occupées
        try:
            t = threading.Thread(target=lambda: done.append(self.store.event(event_id)))
            t.start()
            t.join(1)
        finally:
            for conn in held:
                self.store._pool.put(conn)
        self.assertTrue(done, "la relecture d'un événement attend la requête d'historique")

    def test_latest_uses_indexes(self):
        from monitoring.ird import IrdHealth
        for n in (1, 4):
            self.store.add_marker(1000, MarkerSample(11_220.8e6, -50.0), n=n)
        self.store.add_ird(1000, "A", ird())
        self.store.add_health(1000, "A", IrdHealth(48, 42, False, False, False))
        queries = []
        for conn in self.store._readers:
            conn.set_trace_callback(queries.append)
        latest = self.store.latest()
        for conn in self.store._readers:
            conn.set_trace_callback(None)
        reader = self.store._readers[0]
        self.assertEqual((latest["marker"]["level_dbm"], sorted(latest["markers"]), list(latest["irds"])), (-50.0, [1, 4], ["A"]))
        full_scans = []
        for q in queries:
            for row in reader.execute("EXPLAIN QUERY PLAN " + q):
                detail = row[-1]
                if detail.startswith(("SCAN fsh_marker", "SCAN ird_sat", "SCAN ird_health")):
                    full_scans.append((q, detail))
        self.assertEqual(full_scans, [])


    def _plans(self, conns, fn):
        """Plans d'exécution des requêtes passées par les connexions `conns` pendant fn()."""
        queries = []
        for c in conns:
            c.set_trace_callback(queries.append)
        try:
            fn()
        finally:
            for c in conns:
                c.set_trace_callback(None)
        conn = conns[0]
        return [(q, row[-1]) for q in queries if q.lstrip().upper().startswith(("SELECT", "DELETE", "WITH"))
                for row in conn.execute("EXPLAIN QUERY PLAN " + q)]

    def test_history_reads_only_indexes(self):
        # 30 jours de FSH4 à 4 Hz : des dizaines de millions de lignes. L'historique doit se lire dans les index
        # (couvrants), sans parcourir la table ni chercher les marqueurs présents dans toute la plage.
        for i in range(50):
            for n in (1, 4):
                self.store.add_marker(1000 * i, MarkerSample(11_220.8e6, -50.0), n=n)
            self.store.add_ird(1000 * i, "A", ird())
        plans = self._plans(self.store._readers, lambda: (self.store.marker_series(0, 10**6, 10),
                                                          self.store.ird_series(0, 10**6, 10)))
        bad = [(q, d) for q, d in plans if d.startswith(("SCAN fsh_marker", "SCAN ird_sat"))
               or (d.startswith(("SEARCH fsh_marker", "SEARCH ird_sat")) and "COVERING INDEX" not in d
                   and "LIMIT" not in q.upper())]
        self.assertEqual(bad, [])

    def test_purge_uses_indexes(self):
        from monitoring.ird import IrdHealth
        self.store.add_health(1000, "A", IrdHealth(48, 42, False, False, False))
        self.store.add_status(1000, "fsh", "up", "")
        plans = self._plans([self.store._db], lambda: self.store.purge_before(5000))
        # Parcours complet de table interdit (un parcours d'index couvrant sur la petite table des états est admis).
        self.assertEqual([(q, d) for q, d in plans if d.startswith("SCAN") and "INDEX" not in d], [])

    def test_slow_history_does_not_block_status(self):
        # Une requête d'historique longue (plusieurs dizaines de secondes sur une grosse base) ne doit pas bloquer
        # l'état des appareils que la page demande en même temps.
        self.store.add_ird(1000, "A", ird())
        slow = threading.Thread(target=self.store._all, args=(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 4000000) SELECT COUNT(*) FROM c",))
        slow.start()
        time.sleep(0.05)
        t0 = time.monotonic()
        self.store.latest()
        elapsed = time.monotonic() - t0
        slow.join()
        self.assertLess(elapsed, 0.3)

class MultiIrdTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_ird_series_per_serial(self):
        for ts in (1000, 2000):
            self.store.add_ird(ts, "A", tuned(margin=6.3))
            self.store.add_ird(ts, "B", tuned(rf_khz=11_220_806, pol=None, margin=10.5))
        s = self.store.ird_series(0, 5000, 100)
        self.assertEqual(sorted(s), ["A", "B"])
        self.assertEqual(s["A"]["margin_db"], [6.3, 6.3])
        self.assertEqual(s["A"]["tuning"], {"rf_khz": 12_583_023, "polarization": "vertical",
                                            "symbol_rate": 7_200_000, "roll_off": 0.25})
        self.assertIsNone(s["B"]["tuning"]["polarization"])

    def test_ird_series_segments(self):
        # IRD réglé sur 11 220,8, puis sur 12 583,0, puis revenu : trois périodes distinctes.
        for ts in (1000, 2000, 3000):
            self.store.add_ird(ts, "A", tuned(rf_khz=11_220_806, pol=None))
        for ts in (4000, 5000, 6000):
            self.store.add_ird(ts, "A", tuned(rf_khz=12_583_023 + ts // 1000))  # petites variations : même période
        self.store.add_ird(7000, "A", tuned(rf_khz=11_220_806, pol=None))
        segs = self.store.ird_series(0, 10_000, 100)["A"]["segments"]
        self.assertEqual([(s["from"], s["to"], s["rf_khz"] // 1000, s["polarization"]) for s in segs],
                         [(1000, 3999, 11_220, None), (4000, 6999, 12_583, "vertical"), (7000, 7000, 11_220, None)])

    def test_latest_irds(self):
        self.store.add_ird(1000, "A", tuned(margin=6.0))
        self.store.add_ird(2000, "A", tuned(margin=6.3))
        self.store.add_ird(1500, "B", tuned(margin=9.0))
        latest = self.store.latest()["irds"]
        self.assertEqual({k: v["margin_db"] for k, v in latest.items()}, {"A": 6.3, "B": 9.0})
        self.assertEqual(latest["A"]["rf_khz"], 12_583_023)

    def test_event_source_roundtrip(self):
        i = self.store.open_event(1000, "lock_lost", "critical", "x", source="ird:A")
        self.assertEqual(self.store.event(i)["source"], "ird:A")
        self.assertEqual(self.store.events(0, 2000)[0]["source"], "ird:A")
        self.assertIsNone(self.store.event(self.store.open_event(1000, "fsh_settings", "info", "y"))["source"])

    def test_ird_names(self):
        self.store.set_ird_name("A", "IRD 1", 1000)
        self.store.set_ird_name("A", "Récepteur 1", 2000)
        self.assertEqual(self.store.ird_names(), {"A": "Récepteur 1"})


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "m.db"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())  # la base courante : un test peut la rouvrir

    def test_state_stored_and_aggregated_by_max(self):
        for i in range(100):
            s = replace(tuned(), video_active=(i != 42))
            self.store.add_ird(i * 1000, "A", s)
        raw = self.store.ird_series(0, 200_000, 1000)["A"]
        self.assertEqual(raw["state"][42], 2)
        self.assertEqual(raw["state"][0], 0)
        agg = self.store.ird_series(0, 99_000, 10)["A"]
        self.assertTrue(agg["aggregated"])
        self.assertEqual(sorted(set(agg["state"])), [0, 2])
        self.assertEqual(agg["state"].count(2), 1)

    def test_decode_columns_in_latest(self):
        s = replace(tuned(), video_active=True, video_format="hd_1920x1080i_50", audio_configured=5, audio_active=5,
                    program_number=1, service_name="P100")
        self.store.add_ird(1000, "A", s)
        latest = self.store.latest()["irds"]["A"]
        self.assertEqual((latest["video_format"], latest["audio_active"], latest["service_name"], latest["state"]),
                         ("hd_1920x1080i_50", 5, "P100", 0))
        self.assertIs(latest["video_active"], True)
        self.assertIsNone(latest["health"])

    def test_health_latest(self):
        from monitoring.ird import IrdHealth
        self.store.add_ird(1000, "A", tuned())
        self.store.add_health(1000, "A", IrdHealth(47.0, 41.0, False, False, False))
        self.store.add_health(2000, "A", IrdHealth(48.0, 42.0, True, False, False))
        h = self.store.latest()["irds"]["A"]["health"]
        self.assertEqual((h["fpga_temp"], h["overheat"], h["ts"]), (48.0, True, 2000))

    def test_old_rows_get_state(self):
        self.store.close()
        import sqlite3
        db = sqlite3.connect(self.path)
        db.execute("DROP INDEX IF EXISTS ird_sat_ird_ts_cov")  # base d'avant l'index couvrant
        db.execute("ALTER TABLE ird_sat DROP COLUMN state")
        db.execute("INSERT INTO ird_sat (ts, ird, locked, ts_locked, ber) VALUES (1, 'A', 0, 0, 0)")
        db.execute("INSERT INTO ird_sat (ts, ird, locked, ts_locked, ber) VALUES (2, 'A', 1, 1, 0)")
        db.execute("INSERT INTO ird_sat (ts, ird, locked, ts_locked, ber) VALUES (3, 'A', 1, 1, 0.0001)")
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual([r[0] for r in self.store._all("SELECT state FROM ird_sat ORDER BY ts")], [3, 0, 1])



if __name__ == "__main__":
    unittest.main()
