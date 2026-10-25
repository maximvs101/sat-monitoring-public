import tempfile
import threading
import time
import unittest
from unittest import mock
from dataclasses import replace
from pathlib import Path

from monitoring.app import App
from monitoring.config import CarrierEntry, IrdEntry, PathsConfig, load_config, save_irds
from monitoring.fsh import FshReading, FshSettings, MarkerSample
from monitoring.store import Store
from monitoring.ird import IrdError, IrdHealth, IrdLogMessage, IrdReading, IrdSample

V = "vertical"


def sample(rf_khz=12_583_023, margin=6.3, locked=True, ber=0.0, pol=V):
    return IrdSample(locked=locked, ts_locked=True, cn_db=margin + 5, margin_db=margin, ber=ber, ber_raw="0",
                     power_dbm=-40.0, if_khz=1_983_023, symbol_rate=7_200_000, modcod="dvbs2 8psk 3/4",
                     bitrate_kbps=20000, rf_khz=rf_khz, polarization=pol, roll_off=0.25)


def fsh(markers, span=50e6):
    return FshReading({n: MarkerSample(f, l) for n, (f, l) in markers.items()},
                      FshSettings(span, 300e3, tuple((n, f) for n, (f, _) in markers.items())))


class FakeIrds:
    """Faux parc d'IRD : adresse → (numéro de série, nom), et mesures programmables par série."""

    def __init__(self):
        self.devices = {"10.0.0.1": ("S1", "IRD 1"), "10.0.0.2": ("S2", "IRD 2"), "10.0.0.3": ("S3", "IRD 3")}
        self.samples = {"S1": sample(), "S2": sample(), "S3": sample(rf_khz=11_220_806, pol=None)}
        self.health = {}  # série → IrdHealth renvoyé avec la prochaine lecture
        self.logs = {}    # série → messages du journal renvoyés avec la prochaine lecture
        self.thumbs = {}  # série → aperçu JPEG renvoyé avec chaque lecture

    def factory(self, entry, cfg):
        def identify():
            if entry.host not in self.devices:
                raise IrdError("IRD injoignable")
            return self.devices[entry.host]

        def read():
            serial, _ = identify()
            if entry.serial and serial != entry.serial:
                raise IrdError(f"autre appareil à cette adresse (n° {serial})")
            return IrdReading(self.samples[serial], self.health.get(serial), self.logs.get(serial),
                              self.thumbs.get(serial))

        return read, identify


class AppTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)  # en dernier : après l'arrêt de l'application
        root = Path(self.dir.name)
        self.cfg_path = root / "config.toml"
        self.cfg_path.write_text("[fsh]\nhost = \"10.0.0.9\" # FSH4\n", encoding="utf-8")
        self.irds = FakeIrds()
        self.reading = fsh({1: (11_220.8e6, -50.0), 4: (11_200e6, -67.0)})
        self.fsh_read = lambda: self.reading
        self.closed = 0

    def make(self, entries=(), carriers=(), fsh_pol=V, noise_markers=()):
        save_irds(self.cfg_path, entries)
        cfg = load_config(self.cfg_path)
        cfg = replace(cfg, fsh=replace(cfg.fsh, polarization=fsh_pol), carriers=tuple(carriers),
                      detect=replace(cfg.detect, noise_markers=noise_markers),
                      paths=PathsConfig(db=str(Path(self.dir.name) / "m.db"), log_dir=self.dir.name))
        app = App(cfg, fsh_read=lambda: self.fsh_read(), fsh_close=self.count_close, ird_factory=self.irds.factory,
                  config_path=self.cfg_path, web_dir=Path(self.dir.name))
        self.addCleanup(app.stop)
        return app

    def count_close(self):
        self.closed += 1

    def step_irds(self, app, n=1):
        for _ in range(n):
            for loop in app.ird_loops.values():
                loop.step()

    def test_two_irds_one_carrier_in_status(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("S2", "IRD 2", "10.0.0.2")))
        self.step_irds(app)
        st = app.status()
        self.assertEqual(sorted(st["irds"]), ["S1", "S2"])
        self.assertEqual(st["irds"]["S1"]["name"], "IRD 1")
        [carrier] = st["carriers"]
        self.assertEqual((carrier["key"], carrier["irds"]), ("125830-V", ["S1", "S2"]))

    def test_thumb_kept_and_timestamped_on_change(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        published = []
        app.bus.publish = published.append
        self.irds.thumbs["S1"] = b"\xff\xd8image1"
        self.step_irds(app)
        first = [m for m in published if m["type"] == "ird"][-1]["thumb_ts"]
        self.assertIsNotNone(first)
        self.assertEqual(app.thumbnail("S1"), b"\xff\xd8image1")
        time.sleep(0.01)
        self.step_irds(app)  # même image (IRD figé) : son heure ne bouge pas
        self.assertEqual([m for m in published if m["type"] == "ird"][-1]["thumb_ts"], first)
        time.sleep(0.01)
        self.irds.thumbs["S1"] = b"\xff\xd8image2"
        self.step_irds(app)
        last = [m for m in published if m["type"] == "ird"][-1]
        self.assertGreater(last["thumb_ts"], first)
        self.assertEqual(last["thumb_ts"], last["ts"])
        self.assertEqual(app.thumbnail("S1"), b"\xff\xd8image2")

    def test_thumb_absent_reading_keeps_previous(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.irds.thumbs["S1"] = b"\xff\xd8image1"
        self.step_irds(app)
        del self.irds.thumbs["S1"]  # lecture sans aperçu (entre deux, ou aperçu en échec)
        self.step_irds(app)
        self.assertEqual(app.thumbnail("S1"), b"\xff\xd8image1")

    def test_thumb_dropped_on_remove(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.irds.thumbs["S1"] = b"\xff\xd8image1"
        self.step_irds(app)
        app.remove_ird("S1")
        self.assertIsNone(app.thumbnail("S1"))

    def test_late_reading_after_remove_stores_no_thumb(self):
        # Lecture partie avant le retrait, traitée après : aucune image ne doit renaître pour l'IRD retiré.
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        app.remove_ird("S1")
        app._on_ird("S1", 1_000, IrdReading(sample(), thumb=b"\xff\xd8tard"))
        self.assertIsNone(app.thumbnail("S1"))
        self.assertNotIn("S1", app.thumbs)

    def test_clean_stop_closes_open_events_at_stop_time(self):
        # Arrêt propre avec un appareil injoignable depuis 8 min : l'alerte dure jusqu'à l'arrêt, pas 0 s.
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        now = int(time.time() * 1000)
        app.store.open_event(now - 480_000, "device_down", "critical", "IRD 1 — injoignable", "ird:S1")
        app.stop()
        store = Store(app.cfg.paths.db)
        self.addCleanup(store.close)  # sous Windows, une base restée ouverte empêche d'effacer le dossier de test
        ev = store.events(0, now + 60_000)[0]
        self.assertGreaterEqual(ev["ts_end"], now)

    def test_alive_mark_written(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        before = int(time.time() * 1000)
        app.mark_alive()
        self.assertGreaterEqual(app.store.last_ts(), before)

    def test_status_has_thumb_ts(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("S2", "IRD 2", "10.0.0.2")))
        self.irds.thumbs["S1"] = b"\xff\xd8image1"
        self.step_irds(app)
        irds = app.status()["irds"]
        self.assertIsNotNone(irds["S1"]["latest"]["thumb_ts"])
        self.assertIsNone(irds["S2"]["latest"]["thumb_ts"])

    def test_unlocked_ird_keeps_carrier_symbol_rate(self):
        # Pendant un décrochage, la porteuse garde le débit symbole du dernier lock (largeur de bande, tolérance).
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.step_irds(app)
        # Déverrouillé, la fréquence est la fréquence configurée, à quelques kHz de la mesurée.
        self.irds.samples["S1"] = replace(sample(locked=False, rf_khz=12_583_060), symbol_rate=None, roll_off=None, modcod=None)
        self.step_irds(app)
        [carrier] = app.status()["carriers"]
        self.assertEqual((carrier["symbol_rate"], carrier["roll_off"]), (7_200_000, 0.25))
        # Re-réglé sur une autre porteuse sans lock : pas de débit symbole hérité.
        self.irds.samples["S1"] = replace(sample(locked=False, rf_khz=11_000_000), symbol_rate=None, roll_off=None, modcod=None)
        self.step_irds(app)
        [carrier] = app.status()["carriers"]
        self.assertEqual(carrier["symbol_rate"], None)

    def test_add_ird_identifies_and_saves(self):
        app = self.make()
        entry = app.add_ird("10.0.0.2")
        self.assertEqual(entry, IrdEntry("S2", "IRD 2", "10.0.0.2"))
        self.assertEqual(load_config(self.cfg_path).irds, (entry,))
        self.assertIn("S2", app.ird_loops)
        self.assertEqual(app.store.ird_names()["S2"], "IRD 2")
        self.assertIn("# FSH4", self.cfg_path.read_text(encoding="utf-8"))

    def test_add_same_serial_twice_raises(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        with self.assertRaises(ValueError):
            app.add_ird("10.0.0.1")

    def test_add_unreachable_raises(self):
        app = self.make()
        with self.assertRaises(IrdError):
            app.add_ird("10.0.0.99")
        self.assertEqual(load_config(self.cfg_path).irds, ())

    def test_update_ird_name_and_host(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        app.update_ird("S1", name="Récepteur A", host=None)
        self.assertEqual(load_config(self.cfg_path).irds[0].name, "Récepteur A")
        self.assertEqual(app.store.ird_names()["S1"], "Récepteur A")
        with self.assertRaises(KeyError):
            app.update_ird("S9", name="x", host=None)

    def test_remove_ird_closes_its_alerts(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.irds.samples["S1"] = sample(locked=False)
        self.step_irds(app)
        self.assertEqual([e["kind"] for e in app.store.events(0, 10**13) if e["ts_end"] is None], ["lock_lost"])
        app.remove_ird("S1")
        [ev] = app.store.events(0, 10**13)
        self.assertTrue(ev["detail"].endswith("— IRD retiré"))
        self.assertIsNotNone(ev["ts_end"])
        self.assertNotIn("S1", app.ird_loops)
        self.assertEqual(load_config(self.cfg_path).irds, ())

    def test_unidentified_ird_from_legacy_config_is_identified_at_start(self):
        app = self.make((IrdEntry("", "IRD", "10.0.0.1"),))
        self.assertEqual(app.ird_loops, {})
        app.identify_pending()
        self.assertEqual(list(app.ird_loops), ["S1"])
        self.assertEqual(load_config(self.cfg_path).irds, (IrdEntry("S1", "IRD 1", "10.0.0.1"),))

    def test_alone_alert_end_to_end(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("S2", "IRD 2", "10.0.0.2")))
        self.step_irds(app)
        self.irds.samples["S1"] = sample(locked=False)
        import time
        for _ in range(4):
            self.step_irds(app)
            time.sleep(1.05)
        kinds = [e["kind"] for e in app.store.events(0, 10**13) if e["ts_end"] is None]
        self.assertIn("ird_alone", kinds)

    def test_reading_with_health_and_logs_end_to_end(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.irds.health["S1"] = IrdHealth(48.0, 42.0, False, False, False)
        self.irds.logs["S1"] = [IrdLogMessage(335, "Program is scrambled", "information", False)]
        self.step_irds(app)
        self.assertEqual(app.store.events(0, 10**13), [])  # journal déjà présent : ignoré
        self.irds.health["S1"] = IrdHealth(90.0, 80.0, True, False, False)
        self.irds.logs["S1"] = self.irds.logs["S1"] + [IrdLogMessage(336, "No input activity", "warning", False)]
        self.step_irds(app)
        events = {e["kind"]: e for e in app.store.events(0, 10**13)}
        self.assertEqual(events["hw_fault"]["detail"], "IRD 1 : surchauffe")
        self.assertEqual(events["ird_log"]["detail"], "IRD 1 : No input activity")
        self.assertEqual(events["ird_log"]["source"], "ird:S1")

    def test_status_has_decode_and_health(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.irds.samples["S1"] = replace(sample(), video_active=True, video_format="hd_1920x1080i_50",
                                          audio_configured=5, audio_active=5, program_number=1, service_name="MAIN")
        self.irds.health["S1"] = IrdHealth(48.0, 42.0, False, False, False)
        self.step_irds(app)
        latest = app.status()["irds"]["S1"]["latest"]
        self.assertEqual((latest["video_active"], latest["video_format"], latest["audio_active"], latest["service_name"],
                          latest["state"]), (True, "hd_1920x1080i_50", 5, "MAIN", 0))
        self.assertEqual(latest["health"]["fpga_temp"], 48.0)

    def test_reading_after_removal_ignored(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        app.remove_ird("S1")
        app._on_ird("S1", 10**12, IrdReading(sample(locked=False)))  # lecture arrivée après le retrait
        self.assertEqual(app.store.events(0, 10**13), [])
        self.assertNotIn("S1", app.detector.irds)

    def test_status_config_has_margin_threshold(self):
        app = self.make()
        cfg = app.status()["config"]
        self.assertEqual((cfg["margin_drop_db"], cfg["median_window_s"], cfg["drop_confirm_s"]), (2.0, 60.0, 3.0))

    def test_details_in_status_and_live_not_stored(self):
        from monitoring.ird import AudioVoice, IrdDetails
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        details = IrdDetails(video_codec="h264", audio=(AudioVoice(0, 4112, "mpa", 2, 48000, 384000, True),))
        self.irds.samples["S1"] = replace(sample(), details=details)
        q = app.bus.subscribe()
        self.step_irds(app)
        msg = next(m for m in iter(q.get_nowait, None) if m["type"] == "ird")
        self.assertEqual(msg["details"]["audio"][0]["pid"], 4112)
        latest = app.status()["irds"]["S1"]["latest"]
        self.assertEqual(latest["details"]["video_codec"], "h264")
        self.assertNotIn("details", app.store.latest()["irds"]["S1"])

    def test_removal_during_reading_leaves_no_orphan(self):
        # Retrait de l'IRD pendant le traitement d'une lecture (entre le contrôle et la détection).
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        original, fired = app.carriers, []

        def carriers_then_remove():
            if not fired:
                fired.append(1)
                app.remove_ird("S1")
            return original()

        app.carriers = carriers_then_remove
        app._on_ird("S1", 10**12, IrdReading(sample(locked=False)))
        self.assertNotIn("S1", app.detector.irds)
        self.assertEqual([e for e in app.store.events(0, 10**13) if e["ts_end"] is None], [])
        self.assertNotIn("S1", app.tunings)

    def test_down_after_removal_leaves_no_orphan(self):
        # Un IRD injoignable qu'on retire : sa dernière lecture (en échec) se termine après le retrait.
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        app.remove_ird("S1")
        app._on_state(10**12, "ird:S1", "down", "IRD injoignable : timed out")
        self.assertEqual([e for e in app.store.events(0, 10**13) if e["ts_end"] is None], [])
        self.assertNotIn("ird:S1", app.detector.down_events)

    def test_old_fsh_reading_after_change_is_ignored(self):
        # Lecture de l'ancien FSH4 terminée après le changement d'appareil : pas de « réglage FSH4 modifié » quand le
        # nouveau répond avec d'autres réglages.
        app = self.make()
        app.fsh_loop.step()
        old = app.fsh_loop.read()
        app.set_fsh("10.0.0.99", 5555, None)
        app._on_marker(10**12, old)               # lecture de l'ancien appareil, arrivée en retard
        app.fsh_loop._replacement = None           # le nouveau lecteur est en place
        new = fsh({1: (11_220.8e6, -50.0)}, span=20e6)
        app._on_marker(10**12 + 1000, new)
        self.assertEqual([e for e in app.store.events(0, 10**13) if e["kind"] == "fsh_settings"], [])

    def test_stale_carrier_list_not_published(self):
        # Deux fils calculent les porteuses ; celui qui a la liste la plus ancienne ne doit ni la publier ni fermer
        # les alertes des porteuses de la nouvelle.
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.step_irds(app)
        old = app.carriers()
        self.irds.samples["S1"] = sample(rf_khz=11_000_000)
        self.step_irds(app)
        published = []
        app.bus.publish = published.append
        app._publish_carriers_if_changed(old)
        self.assertEqual([m for m in published if m["type"] == "carriers"], [])

    def test_add_ird_not_tracked_when_config_cannot_be_saved(self):
        # config.toml bloqué (antivirus sous Windows) : l'ajout échoue, et l'IRD n'est pas suivi en douce.
        app = self.make()
        with mock.patch("monitoring.app.save_irds", side_effect=PermissionError("config.toml verrouillé")):
            with self.assertRaises(PermissionError):
                app.add_ird("10.0.0.2")
        self.assertNotIn("S2", app.ird_loops)
        self.assertEqual(app.cfg.irds, ())

    def test_status_fsh_span_and_rbw(self):
        app = self.make()
        self.assertEqual((app.status()["fsh"]["span_hz"], app.status()["fsh"]["rbw_hz"]), (None, None))
        app.fsh_loop.step()
        fsh_status = app.status()["fsh"]
        self.assertEqual((fsh_status["span_hz"], fsh_status["rbw_hz"]), (50e6, 300e3))

    def test_carriers_message_published_when_grouping_changes(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        q = app.bus.subscribe()
        self.step_irds(app)
        msgs = []
        while not q.empty():
            msgs.append(q.get())
        self.assertEqual([m["carriers"][0]["key"] for m in msgs if m["type"] == "carriers"], ["125830-V"])
        self.step_irds(app)
        self.assertFalse(any(q.get()["type"] == "carriers" for _ in range(q.qsize())))

    def test_marker_drop_uses_center_marker_of_carrier(self):
        entries = (IrdEntry("S3", "IRD 3", "10.0.0.3"),)
        app = self.make(entries)
        self.step_irds(app)
        t = 0
        for level in [-50.0] * 60 + [-56.0] * 3:
            self.reading = fsh({1: (11_220.8e6, level), 2: (11_213e6, -50.0), 4: (11_200e6, -67.0)})
            t += 1000
            app._on_marker(t, app.fsh_loop.read())
        [ev] = [e for e in app.store.events(0, 10**13) if e["kind"] == "marker_drop"]
        self.assertEqual(ev["source"], "porteuse:112208-?")

    def test_if_markers_warning_in_status(self):
        app = self.make((IrdEntry("S3", "IRD 3", "10.0.0.3"),))
        self.reading = fsh({1: (1_470.8e6, -50.0)})
        self.step_irds(app)
        app.fsh_loop.step()
        self.assertTrue(app.status()["marker_map"]["if_warning"])

    def test_noise_from_markers_outside_carriers(self):
        app = self.make((IrdEntry("S3", "IRD 3", "10.0.0.3"),))
        self.step_irds(app)
        t = 0
        for noise in [-67.0] * 60 + [-62.0] * 3:
            t += 1000
            app._on_marker(t, fsh({1: (11_220.8e6, -50.0), 4: (11_200e6, noise), 5: (11_242e6, noise)}))
        self.assertIn("noise_rise", [e["kind"] for e in app.store.events(0, 10**13)])

    def test_set_fsh_saves_polarization(self):
        app = self.make()
        app.set_fsh("10.0.0.8", 5025, "horizontal")
        fsh_cfg = load_config(self.cfg_path).fsh
        self.assertEqual((fsh_cfg.host, fsh_cfg.port, fsh_cfg.polarization), ("10.0.0.8", 5025, "horizontal"))
        self.assertEqual(app.store.events(0, 10**13)[0]["kind"], "devices_changed")

    def test_carriers_rename_add_remove(self):
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        self.step_irds(app)
        app.set_carrier_name("125830-V", "Client A")
        self.assertEqual(app.carriers()[0].name, "Client A")
        self.assertEqual(load_config(self.cfg_path).carriers[0].name, "Client A")
        app.add_carrier(CarrierEntry("Sans IRD", 11685.0, "horizontal", 27.5, True))
        keys = [c.key for c in app.carriers()]
        self.assertEqual(keys, ["116850-H", "125830-V"])
        with self.assertRaises(ValueError):
            app.remove_carrier("125830-V")  # automatique : on ne la supprime pas
        app.remove_carrier("116850-H")
        self.assertEqual([c.key for c in app.carriers()], ["125830-V"])

    def test_concurrent_add_remove_while_reading(self):
        # Ajout / retrait d'un IRD pendant que la collecte et la page lisent l'état de l'application.
        import threading
        import time as _time
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("S3", "IRD 3", "10.0.0.3")))
        extra = [f"10.0.1.{i}" for i in range(30)]
        for i, host in enumerate(extra):
            self.irds.devices[host] = (f"X{i}", f"IRD X{i}")
            self.irds.samples[f"X{i}"] = sample(rf_khz=12_583_023 + i)
        self.step_irds(app)
        errors, stop = [], threading.Event()

        def churn():
            while not stop.is_set():
                try:
                    for host in extra:
                        app.add_ird(host)
                    for i in range(len(extra)):
                        app._on_ird(f"X{i}", 10**12, IrdReading(sample()))
                    for i in range(len(extra)):
                        app.remove_ird(f"X{i}")
                except Exception as e:  # noqa: BLE001 — tout est une erreur ici
                    errors.append(repr(e))

        def read():
            t = 10**12
            while not stop.is_set():
                try:
                    t += 1
                    app._on_ird("S1", t, IrdReading(sample()))
                    app.status()
                    app._on_marker(t, self.reading)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

        import sys
        saved = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # changements de fil très fréquents : rend le conflit observable
        self.addCleanup(sys.setswitchinterval, saved)
        threads = [threading.Thread(target=churn), threading.Thread(target=read), threading.Thread(target=read)]
        for th in threads:
            th.start()
        _time.sleep(2)
        stop.set()
        for th in threads:
            th.join(10)
        self.assertEqual(errors[:3], [])

    def test_polarization_override_groups_and_is_stored(self):
        self.irds.samples["S1"] = sample(pol=None)
        self.irds.samples["S2"] = sample(pol=None)
        app = self.make((IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("S2", "IRD 2", "10.0.0.2")))
        self.step_irds(app)
        [c] = app.status()["carriers"]
        self.assertEqual((c["key"], c["unknown_polarization"]), ("125830-?", ["S1", "S2"]))
        app.update_ird("S1", name=None, host=None, polarization="horizontal")
        app.update_ird("S2", name=None, host=None, polarization="vertical")
        self.step_irds(app)
        keys = sorted(c["key"] for c in app.status()["carriers"])
        self.assertEqual(keys, ["125830-H", "125830-V"])  # transpondeurs V et H de même fréquence : séparés
        self.assertEqual(app.store.latest()["irds"]["S1"]["polarization"], "horizontal")
        self.assertEqual(load_config(self.cfg_path).irds[0].polarization, "horizontal")
        app.update_ird("S1", name=None, host=None, polarization=None)
        self.assertIsNone(load_config(self.cfg_path).irds[0].polarization)

    def test_no_fsh_in_config(self):
        # Site sans FSH4 : aucune lecture, aucun « injoignable », état « absent ».
        self.cfg_path.write_text('[fsh]\nhost = ""\n', encoding="utf-8")
        reads = []
        self.fsh_read = lambda: reads.append(1)
        app = self.make()
        app.fsh_loop.step()
        app.fsh_loop.step()
        self.assertEqual(reads, [])
        self.assertEqual(app.status()["fsh"]["state"], "absent")
        self.assertEqual([e for e in app.store.events(0, 10**13) if e["kind"] == "device_down"], [])

    def test_fsh_removed_then_added_back(self):
        app = self.make()
        app.fsh_loop.state = "down"
        app.store.add_status(1000, "fsh", "down", "FSH4 injoignable")
        app._on_state(1000, "fsh", "down", "FSH4 injoignable")
        app.set_fsh("", 5555, None)
        app.fsh_loop.step()
        self.assertEqual(app.fsh_loop.state, "absent")
        self.assertTrue(all(e["ts_end"] is not None for e in app.store.events(0, 10**13)))
        app.set_fsh("10.0.0.8", 5555, None)
        self.assertFalse(app.fsh_loop._paused.is_set())  # lu de nouveau à la prochaine itération

    def test_status_drops_measurements_of_previous_fsh(self):
        # Nouveau FSH4 (ou retiré) : la « dernière mesure » affichée n'est pas celle de l'ancien appareil.
        app = self.make()
        app.store.add_markers(1000, {1: MarkerSample(11_220.8e6, -50.0)})
        self.assertEqual(app.status()["marker"]["ts"], 1000)
        app.set_fsh("", 5555, None)
        st = app.status()
        self.assertIsNone(st["marker"])
        self.assertEqual(st["markers"], {})

    def test_pause_closes_fsh(self):
        app = self.make()
        app.fsh_loop.pause()
        app.fsh_loop.step()
        self.assertEqual(self.closed, 1)

    def test_start_closes_stale_events(self):
        app = self.make()
        app.store.add_ird(1000, "S1", sample())
        app.store.open_event(900, "lock_lost", "critical", "x", "ird:S1")
        app.start()
        self.assertEqual(app.store.events(0, 10**13)[0]["ts_end"], 1000)

    def test_start_purges_old_data_in_background(self):
        # Une grosse purge (passage de « tout garder » à 30 jours sur un an de mesures) ne doit pas retarder la
        # collecte : elle se fait dans le fil de purge, dès le démarrage.
        app = self.make()
        app.store.add_ird(1000, "S1", sample())
        app.cfg = replace(app.cfg, paths=replace(app.cfg.paths, retention_days=1))
        release, started = threading.Event(), threading.Event()
        original = app.store.purge_before

        def slow_purge(ts):
            started.set()
            release.wait(5)
            original(ts)

        app.store.purge_before = slow_purge
        t0 = time.monotonic()
        app.start()
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertTrue(app.fsh_loop.is_alive())
        self.assertTrue(started.wait(2))
        release.set()
        for _ in range(50):
            if app.store._all("SELECT COUNT(*) FROM ird_sat WHERE ts = 1000")[0][0] == 0:
                break
            time.sleep(0.05)
        self.assertEqual(app.store._all("SELECT COUNT(*) FROM ird_sat WHERE ts = 1000")[0][0], 0)


if __name__ == "__main__":
    unittest.main()
