import tempfile
from dataclasses import replace
import unittest
from pathlib import Path

from monitoring.carriers import Carrier
from monitoring.config import DetectConfig
from monitoring.detect import Detector
from monitoring.fsh import MarkerSample
from monitoring.store import Store


def ird(margin=10.5, locked=True, ber=0.0, ts_locked=True):
    from monitoring.ird import IrdSample
    return IrdSample(locked=locked, ts_locked=ts_locked, cn_db=margin + 5.2, margin_db=margin, ber=ber, ber_raw=str(ber),
                     power_dbm=-38.0, if_khz=1470806, symbol_rate=24760000, modcod="dvbs2 qpsk 5/6", bitrate_kbps=40969)


CARRIER = Carrier("112208-V", "BBC", 11_220_806, "vertical", 24_760_000, 0.2, ("A",), False)
SHARED = Carrier("125830-V", "12 583,0 V", 12_583_023, "vertical", 7_200_000, 0.25, ("A", "B"), False)
OTHER = Carrier("112208-V", "11 220,8 V", 11_220_806, "vertical", 24_760_000, 0.2, ("B",), False)


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "m.db")
        self.det = Detector(self.store, DetectConfig())
        self.det.set_name("A", "IRD 1")
        self.t = 0

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def feed(self, sample, seconds=1, kind="ird", step_ms=1000):
        out = []
        for _ in range(seconds):
            self.t += step_ms
            if kind == "ird":
                out += self.det.on_ird(self.t, "A", "IRD A", sample)
            else:
                out += self.det.on_carrier_level(self.t, CARRIER, sample.level_dbm)
        return out

    def kinds(self, open_only=False):
        return [e["kind"] for e in self.store.events(0, 10**12) if not open_only or e["ts_end"] is None]

    def test_lock_lost_open_close(self):
        self.feed(ird(), 5)
        changed = self.feed(ird(locked=False))
        self.assertEqual([(e["kind"], e["severity"], e["ts_end"]) for e in changed], [("lock_lost", "critical", None)])
        changed = self.feed(ird())
        self.assertEqual(changed[0]["ts_end"], self.t)

    def test_margin_drop(self):
        self.feed(ird(10.5), 60)
        self.feed(ird(8.0), 3)
        self.assertEqual(self.kinds(open_only=True), ["margin_drop"])
        self.feed(ird(9.4), 3)
        self.assertEqual(self.kinds(open_only=True), ["margin_drop"])
        changed = self.feed(ird(9.6), 3)
        self.assertEqual(changed[0]["kind"], "margin_drop")
        self.assertIsNotNone(changed[0]["ts_end"])
        self.assertIn("référence 10,5 dB", self.store.events(0, 10**12)[0]["detail"])

    def test_no_drop_before_min_history(self):
        self.feed(ird(10.5), 5)
        self.feed(ird(5.0), 3)
        self.assertEqual(self.kinds(), [])

    def test_unlocked_only_lock_lost(self):
        self.feed(ird(10.5), 60)
        self.feed(ird(margin=0.0, locked=False, ber=0.5))
        self.assertEqual(self.kinds(), ["lock_lost"])

    def test_ber_high_needs_5s_clear(self):
        self.feed(ird(), 20)
        self.feed(ird(ber=1e-5))
        self.assertEqual(self.kinds(open_only=True), ["ber_high"])
        self.feed(ird(ber=0.0), 4)
        self.assertEqual(self.kinds(open_only=True), ["ber_high"])
        self.feed(ird(ber=0.0))
        self.assertEqual(self.kinds(open_only=True), [])

    def test_ts_lock_lost(self):
        self.feed(ird(), 3)
        self.feed(ird(ts_locked=False))
        self.assertEqual(self.kinds(open_only=True), ["ts_lock_lost"])

    def test_marker_drop(self):
        self.feed(MarkerSample(11.2e9, -40.8), 60, kind="fsh")
        self.feed(MarkerSample(11.2e9, -44.0), 3, kind="fsh")
        self.assertEqual(self.kinds(open_only=True), ["marker_drop"])
        self.feed(MarkerSample(11.2e9, -41.5), 3, kind="fsh")
        self.assertEqual(self.kinds(open_only=True), [])

    def test_no_stale_reference_after_gap(self):
        # 60 s à -40 dBm, pause de 30 min, puis niveau stable à -55 dBm : pas de chute ouverte.
        self.feed(MarkerSample(11.2e9, -40.0), 60, kind="fsh")
        self.t += 30 * 60 * 1000
        self.feed(MarkerSample(11.2e9, -55.0), 100, kind="fsh")
        self.assertEqual(self.kinds(), [])

    def test_settings_change_closes_marker_drop(self):
        self.feed(MarkerSample(11.2e9, -40.2), 60, kind="fsh")
        self.feed(MarkerSample(11.2e9, -45.0), 3, kind="fsh")
        self.assertEqual(self.kinds(open_only=True), ["marker_drop"])
        self.t += 1000
        changed = self.det.on_fsh_settings(self.t, "marqueur 11 220,8 MHz, span 50 MHz, RBW 300 kHz")
        kinds = {e["kind"]: e for e in changed}
        self.assertIsNotNone(kinds["marker_drop"]["ts_end"])
        self.assertIn("réglage FSH4 modifié", kinds["marker_drop"]["detail"])
        self.assertEqual(kinds["fsh_settings"]["severity"], "info")
        self.assertEqual(kinds["fsh_settings"]["ts_end"], self.t)
        self.assertEqual(self.kinds(open_only=True), [])
        # Nouvelle référence : le nouveau niveau normal n'ouvre pas de chute.
        self.feed(MarkerSample(11.2208e9, -51.0), 60, kind="fsh")
        self.assertEqual(self.kinds(open_only=True), [])

    def test_devices_changed_closes_fsh_alerts(self):
        self.feed(MarkerSample(11.22e9, -50.0), 60, kind="fsh")
        self.feed(MarkerSample(11.22e9, -56.0), 3, kind="fsh")
        self.feed(ird(locked=False))
        self.det.on_state(self.t, "fsh", "down", "délai")
        self.t += 1000
        changed = self.det.on_devices_changed(self.t, "FSH4 10.0.0.5:5555")
        closed = [e for e in changed if e["kind"] != "devices_changed"]
        self.assertEqual(sorted(e["kind"] for e in closed), ["device_down", "marker_drop"])
        self.assertTrue(all("appareils changés" in e["detail"] for e in closed))
        self.assertEqual(self.kinds(open_only=True), ["lock_lost"])  # les IRD ne sont pas concernés
        info = [e for e in changed if e["kind"] == "devices_changed"][0]
        self.assertEqual((info["severity"], info["ts_end"], info["source"]), ("info", self.t, "fsh"))

    def noisy(self, center, seconds, sigma=1.2, seed=1):
        import random
        rnd = random.Random(seed)
        for _ in range(seconds * 4):  # 4 lectures/s comme le FSH4
            self.feed(MarkerSample(11.2208e9, rnd.gauss(center, sigma)), kind="fsh", step_ms=250)

    def test_noise_alone_no_marker_drop(self):
        # Bruit relevé le 17/10 avec la RBW réduite : écart-type 1,2 dB autour de -50,4 dBm.
        self.noisy(-50.4, 600)
        self.assertEqual(self.kinds(), [])

    def test_single_low_point_no_drop(self):
        self.feed(MarkerSample(11.2e9, -50.0), 60, kind="fsh")
        self.feed(MarkerSample(11.2e9, -56.0), kind="fsh")
        self.feed(MarkerSample(11.2e9, -50.0), 5, kind="fsh")
        self.assertEqual(self.kinds(), [])

    def test_real_drop_in_noise_dated_at_start(self):
        self.noisy(-50.4, 120)
        start = self.t + 250
        self.noisy(-56.0, 6, seed=2)
        events = self.store.events(0, 10**12)
        self.assertEqual([e["kind"] for e in events], ["marker_drop"])
        self.assertLess(abs(events[0]["ts_start"] - start), 3000)
        self.noisy(-50.4, 6, seed=3)
        self.assertEqual(self.kinds(open_only=True), [])

    def test_confirm_zero_reacts_to_single_point(self):
        det = Detector(self.store, DetectConfig(drop_confirm_s=0.0))
        for i in range(60):
            det.on_carrier_level(1000 * (i + 1), CARRIER, -50.0)
        det.on_carrier_level(61_000, CARRIER, -56.0)
        self.assertEqual(self.kinds(open_only=True), ["marker_drop"])

    def test_noise_rise(self):
        for _ in range(60):
            self.t += 1000
            self.det.on_noise(self.t, -62.0)
        for _ in range(3):
            self.t += 1000
            changed = self.det.on_noise(self.t, -57.5)
        self.assertEqual(self.kinds(open_only=True), ["noise_rise"])
        ev = self.store.events(0, 10**12)[0]
        self.assertEqual(ev["severity"], "warning")
        self.assertIn("plancher de bruit -57,5 dBm (référence -62,0 dBm)", ev["detail"])
        for _ in range(3):
            self.t += 1000
            self.det.on_noise(self.t, -60.5)
        self.assertEqual(self.kinds(open_only=True), ["noise_rise"])  # pas encore à 1 dB de la référence
        for _ in range(3):
            self.t += 1000
            self.det.on_noise(self.t, -61.5)
        self.assertEqual(self.kinds(open_only=True), [])

    def test_noise_single_spike_ignored(self):
        for _ in range(60):
            self.t += 1000
            self.det.on_noise(self.t, -62.0)
        self.t += 1000
        self.det.on_noise(self.t, -50.0)
        for _ in range(5):
            self.t += 1000
            self.det.on_noise(self.t, -62.0)
        self.assertEqual(self.kinds(), [])

    def test_settings_change_resets_noise(self):
        for _ in range(60):
            self.t += 1000
            self.det.on_noise(self.t, -62.0)
        for _ in range(3):
            self.t += 1000
            self.det.on_noise(self.t, -57.0)
        self.t += 1000
        changed = self.det.on_fsh_settings(self.t, "réglage")
        self.assertIn("noise_rise", [e["kind"] for e in changed if e["ts_end"]])
        self.assertEqual(self.kinds(open_only=True), [])

    def test_reference_frozen_during_and_after_event(self):
        # Essai du 17/10 : un retour partiel refermait l'alerte et la référence absorbait le dépointage.
        fsh = lambda level, s: self.feed(MarkerSample(11.22e9, level), s, kind="fsh")
        fsh(-50.0, 60)
        fsh(-56.0, 70)  # plus d'une minute, comme lors de l'essai
        fsh(-50.5, 5)   # retour partiel : l'alerte se ferme
        fsh(-56.0, 20)  # nouveau dépointage
        events = self.store.events(0, 10**12)
        self.assertEqual([e["kind"] for e in events], ["marker_drop", "marker_drop"])
        self.assertIn("référence -50,0 dBm", events[0]["detail"])  # et non -53 ou -55
        fsh(-50.5, 5)
        self.assertEqual(self.kinds(open_only=True), [])

    def test_reference_used_again_after_quiet_minute(self):
        fsh = lambda level, s: self.feed(MarkerSample(11.22e9, level), s, kind="fsh")
        fsh(-50.0, 60)
        fsh(-56.0, 10)
        fsh(-50.0, 70)  # une minute après la fermeture, le niveau normal sert de nouveau de référence
        fsh(-46.0, 70)  # nouveau niveau normal (réglage modifié à la main, par exemple)
        fsh(-50.0, 5)   # -4 dB sous le nouveau niveau : chute
        self.assertEqual(self.kinds(open_only=True), ["marker_drop"])
        self.assertIn("référence -46,0 dBm", self.store.events(0, 10**12)[0]["detail"])

    # --- IRD multiples et porteuses ---

    def step(self, a=None, b=None, carriers=(SHARED,), reachable=("A", "B")):
        """Une seconde : une lecture par IRD fourni, puis l'évaluation « IRD seul en défaut »."""
        self.t += 1000
        out = []
        if a is not None:
            out += self.det.on_ird(self.t, "A", "IRD 1", a)
        if b is not None:
            out += self.det.on_ird(self.t, "B", "IRD 2", b)
        out += self.det.on_alone(self.t, list(carriers), set(reachable))
        return out

    def alone(self, open_only=True):
        return [e for e in self.store.events(0, 10**12) if e["kind"] == "ird_alone" and (not open_only or e["ts_end"] is None)]

    def test_rules_are_per_ird(self):
        self.step(ird(), ird())
        self.step(ird(locked=False), ird())
        lost = [e for e in self.store.events(0, 10**12) if e["kind"] == "lock_lost"]
        self.assertEqual([(e["source"], e["detail"]) for e in lost], [("ird:A", "IRD 1 : démodulateur déverrouillé")])

    def test_margin_detail_prefixed_with_ird_name(self):
        for _ in range(60):
            self.step(ird(10.5), ird(10.5))
        for _ in range(3):
            self.step(ird(7.0), ird(10.5))
        drop = [e for e in self.store.events(0, 10**12) if e["kind"] == "margin_drop"][0]
        self.assertTrue(drop["detail"].startswith("IRD 1 : marge"))
        self.assertEqual(drop["source"], "ird:A")

    def test_alone_opens_after_3s(self):
        self.step(ird(), ird())
        self.step(ird(ber=1e-4), ird())  # BER élevé ouvert tout de suite sur A
        self.step(ird(ber=1e-4), ird())
        self.assertEqual(self.alone(), [])
        self.step(ird(ber=1e-4), ird())
        self.step(ird(ber=1e-4), ird())
        [ev] = self.alone()
        self.assertEqual(ev["detail"], "IRD 1 seul en défaut sur 12 583,0 V (IRD 2 normal) — cause probable : équipement ou câblage")
        self.assertEqual((ev["severity"], ev["source"]), ("warning", "ird:A"))

    def test_alone_closes_when_other_degrades(self):
        for _ in range(5):
            self.step(ird(ber=1e-4), ird())
        self.assertEqual(len(self.alone()), 1)
        self.step(ird(ber=1e-4), ird(ber=1e-4))
        self.assertEqual(self.alone(), [])
        self.assertEqual(len(self.alone(open_only=False)), 1)

    def test_alone_closes_when_ird_recovers(self):
        for _ in range(5):
            self.step(ird(ber=1e-4), ird())
        for _ in range(7):
            self.step(ird(), ird())
        self.assertEqual(self.alone(), [])

    def test_alone_not_for_unreachable(self):
        for _ in range(6):
            self.step(ird(ber=1e-4), None, reachable=("A",))  # B injoignable : rien à comparer
        self.assertEqual(self.alone(open_only=False), [])

    def test_alone_not_across_carriers(self):
        carriers = (Carrier("125830-V", "12 583,0 V", 12_583_023, "vertical", 7_200_000, 0.25, ("A",), False), OTHER)
        for _ in range(6):
            self.step(ird(ber=1e-4), ird(), carriers=carriers)
        self.assertEqual(self.alone(open_only=False), [])

    def test_alone_when_unlocked_ird_keeps_its_carrier(self):
        for _ in range(5):
            self.step(ird(locked=False), ird())
        [ev] = self.alone()
        self.assertEqual(ev["severity"], "critical")

    def test_carrier_level_drop_per_carrier(self):
        for i in range(60):
            self.t += 1000
            self.det.on_carrier_level(self.t, CARRIER, -50.0)
            self.det.on_carrier_level(self.t, SHARED, -60.0)
        for i in range(3):
            self.t += 1000
            self.det.on_carrier_level(self.t, CARRIER, -56.0)
            self.det.on_carrier_level(self.t, SHARED, -60.0)
        [drop] = self.store.events(0, 10**12)
        self.assertEqual(drop["source"], "porteuse:112208-V")
        self.assertTrue(drop["detail"].startswith("BBC : niveau marqueur"))

    def test_prune_closes_vanished_carrier_alert(self):
        for _ in range(60):
            self.t += 1000
            self.det.on_carrier_level(self.t, CARRIER, -50.0)
        for _ in range(3):
            self.t += 1000
            self.det.on_carrier_level(self.t, CARRIER, -56.0)
        self.t += 1000
        [ev] = self.det.prune_carriers(self.t, {"125830-V"})
        self.assertEqual(ev["kind"], "marker_drop")
        self.assertTrue(ev["detail"].endswith("— porteuse disparue"))
        self.assertEqual(self.kinds(open_only=True), [])
        self.assertEqual(self.det.prune_carriers(self.t, set()), [])

    def test_remove_ird_closes_alerts(self):
        for _ in range(5):
            self.step(ird(locked=False), ird())
        self.det.on_state(self.t, "ird:A", "down", "délai")
        self.t += 1000
        changed = self.det.remove_ird(self.t, "A")
        self.assertEqual(sorted(e["kind"] for e in changed), ["device_down", "ird_alone", "lock_lost"])
        self.assertTrue(all(e["detail"].endswith("— IRD retiré") for e in changed))
        self.assertEqual(self.kinds(open_only=True), [])

    def test_ird_down_detail_has_name_and_reason(self):
        self.det.set_name("S9", "IRD 9")  # jamais lu : le nom vient de la configuration
        [ev] = self.det.on_state(1000, "ird:S9", "down", "autre appareil à cette adresse (n° 1000-00002)")
        self.assertEqual(ev["detail"], "IRD 9 — autre appareil à cette adresse (n° 1000-00002)")
        self.assertEqual(ev["source"], "ird:S9")

    # --- État des IRD : décodage, matériel, journal ---

    def dec(self, **kw):
        base = dict(video_active=True, audio_configured=5, audio_active=5, scrambled=True, descrambled=True,
                    program_number=1)
        base.update(kw)
        return replace(ird(), **base)

    def run_ird(self, seconds, **kw):
        out = []
        for _ in range(seconds):
            self.t += 1000
            out += self.det.on_ird(self.t, "A", "IRD 1", self.dec(**kw))
        return out

    def open_kinds(self):
        return sorted(e["kind"] for e in self.store.events(0, 10**12) if e["ts_end"] is None)

    def test_video_lost_after_3s(self):
        self.run_ird(2)
        self.run_ird(3, video_active=False)
        self.assertEqual(self.open_kinds(), [])
        self.run_ird(1, video_active=False)
        [ev] = self.store.events(0, 10**12)
        self.assertEqual((ev["kind"], ev["severity"], ev["detail"]), ("video_lost", "critical", "IRD 1 : image perdue"))
        self.run_ird(1)
        self.assertEqual(self.open_kinds(), [])

    def test_interrupted_delay_restarts(self):
        self.run_ird(2, video_active=False)
        self.run_ird(1)
        self.run_ird(2, video_active=False)
        self.assertEqual(self.open_kinds(), [])

    def test_audio_silent_only_with_image(self):
        self.run_ird(5, audio_active=0)
        [ev] = self.store.events(0, 10**12)
        self.assertEqual((ev["kind"], ev["severity"], ev["detail"]), ("audio_silent", "warning", "IRD 1 : son absent (0/5 voies)"))
        self.run_ird(1)
        self.run_ird(5, audio_active=0, video_active=False)
        self.assertEqual(self.open_kinds(), ["video_lost"])

    def test_no_audio_configured_no_alert(self):
        self.run_ird(10, audio_configured=0, audio_active=0)
        self.run_ird(10, audio_configured=None, audio_active=None)
        self.assertEqual(self.store.events(0, 10**12), [])

    def test_not_descrambled(self):
        self.run_ird(5, descrambled=False)
        self.assertEqual(self.open_kinds(), ["not_descrambled"])
        self.run_ird(1, scrambled=False, descrambled=False)  # programme passé en clair
        self.assertEqual(self.open_kinds(), [])

    def test_ts_stopped(self):
        self.run_ird(5, bitrate_kbps=0)
        self.assertEqual(self.open_kinds(), ["ts_stopped"])
        self.assertEqual(self.store.events(0, 10**12)[0]["detail"], "IRD 1 : flux d'entrée arrêté")

    def test_lock_lost_closes_decode_alerts(self):
        self.run_ird(5, video_active=False)
        self.assertEqual(self.open_kinds(), ["video_lost"])
        self.run_ird(5, locked=False, video_active=False)
        self.assertEqual(self.open_kinds(), ["lock_lost"])
        closed = [e for e in self.store.events(0, 10**12) if e["kind"] == "video_lost"][0]
        self.assertTrue(closed["detail"].endswith("— réception perdue"))
        self.run_ird(3, video_active=False)  # lock revenu, image toujours absente : délai de 3 s à nouveau
        self.assertEqual(self.open_kinds(), [])
        self.run_ird(1, video_active=False)
        self.assertEqual(self.open_kinds(), ["video_lost"])

    def test_hw_fault_detail(self):
        from monitoring.ird import IrdHealth
        self.assertEqual(self.det.on_health(1000, "A", IrdHealth(48, 42, False, False, False)), [])
        [ev] = self.det.on_health(2000, "A", IrdHealth(90, 80, True, True, False))
        self.assertEqual((ev["kind"], ev["severity"], ev["detail"]), ("hw_fault", "critical", "IRD 1 : surchauffe, ventilateur"))
        self.assertEqual(self.det.on_health(3000, "A", IrdHealth(91, 80, True, True, False)), [])
        [closed] = self.det.on_health(4000, "A", IrdHealth(60, 50, False, False, False))
        self.assertEqual(closed["ts_end"], 4000)

    def msg(self, n_id, text="x", level="warning", ended=False):
        from monitoring.ird import IrdLogMessage
        return IrdLogMessage(n_id, text, level, ended)

    def test_logs_backlog_ignored(self):
        self.assertEqual(self.det.on_logs(1000, "A", [self.msg(335, "Program is scrambled", "information")]), [])
        self.assertEqual(self.det.on_logs(2000, "A", [self.msg(335, "Program is scrambled", "information")]), [])
        [ev] = self.det.on_logs(3000, "A", [self.msg(335, level="information"), self.msg(336, "TS discontinuity")])
        self.assertEqual((ev["kind"], ev["severity"], ev["detail"]), ("ird_log", "warning", "IRD 1 : TS discontinuity"))

    def test_log_without_text(self):
        self.det.on_logs(1000, "A", [])
        [ev] = self.det.on_logs(2000, "A", [self.msg(7, "", "warning")])
        self.assertEqual(ev["detail"], "IRD 1 : message sans texte (warning)")

    def test_info_log_closed_immediately(self):
        self.det.on_logs(1000, "A", [])
        [ev] = self.det.on_logs(2000, "A", [self.msg(400, "Program is scrambled", "information")])
        self.assertEqual((ev["severity"], ev["ts_end"]), ("info", 2000))

    def test_warning_log_open_until_ended(self):
        self.det.on_logs(1000, "A", [])
        self.det.on_logs(2000, "A", [self.msg(401, "No input activity", "warning")])
        self.assertEqual(self.open_kinds(), ["ird_log"])
        [closed] = self.det.on_logs(3000, "A", [self.msg(401, "No input activity", "warning", ended=True)])
        self.assertEqual(closed["ts_end"], 3000)
        self.det.on_logs(4000, "A", [self.msg(402, "Alarme", "error")])
        self.assertEqual(self.store.events(0, 10**12)[0]["severity"], "critical")
        self.det.on_logs(5000, "A", [])  # message disparu du journal
        self.assertEqual(self.open_kinds(), [])

    def test_log_ids_going_back_rebaseline(self):
        self.det.on_logs(1000, "A", [self.msg(500), self.msg(501)])
        self.assertEqual(self.det.on_logs(2000, "A", [self.msg(3), self.msg(4)]), [])  # IRD redémarré
        [ev] = self.det.on_logs(3000, "A", [self.msg(3), self.msg(4), self.msg(5, "nouveau")])
        self.assertEqual(ev["detail"], "IRD 1 : nouveau")

    def test_alone_video_only_same_program(self):
        def both(a_kw, b_prog):
            for _ in range(5):
                self.t += 1000
                self.det.on_ird(self.t, "A", "IRD 1", self.dec(**a_kw))
                self.det.on_ird(self.t, "B", "IRD 2", self.dec(program_number=b_prog))
                self.det.on_alone(self.t, [SHARED], {"A", "B"})
        both({"video_active": False}, 2)
        self.assertNotIn("ird_alone", self.open_kinds())
        self.t += 60_000
        both({"video_active": False}, 1)
        self.assertIn("ird_alone", self.open_kinds())
        alone = [e for e in self.store.events(0, 10**12) if e["kind"] == "ird_alone"][0]
        self.assertEqual(alone["severity"], "critical")

    def test_down_closes_decode_and_hw(self):
        from monitoring.ird import IrdHealth
        self.run_ird(5, video_active=False)
        self.det.on_health(self.t, "A", IrdHealth(90, 80, True, False, False))
        self.t += 1000
        changed = self.det.on_state(self.t, "ird:A", "down", "délai")
        closed = sorted(e["kind"] for e in changed if e["ts_end"] is not None)
        self.assertEqual(closed, ["hw_fault", "video_lost"])
        self.assertTrue(all(e["detail"].endswith("— IRD injoignable") for e in changed if e["ts_end"]))
        self.assertEqual(self.open_kinds(), ["device_down"])

    def test_remove_closes_new_alerts(self):
        self.run_ird(5, descrambled=False)
        self.det.on_logs(self.t, "A", [])
        self.det.on_logs(self.t + 1, "A", [self.msg(9)])
        changed = self.det.remove_ird(self.t + 2, "A")
        self.assertEqual(sorted(e["kind"] for e in changed), ["ird_log", "not_descrambled"])
        self.assertEqual(self.open_kinds(), [])

    def test_unknown_decode_keeps_open_alert(self):
        self.run_ird(5, video_active=False)
        self.assertEqual(self.run_ird(3, video_active=None), [])  # champ absent : inconnu, pas « image revenue »
        self.assertEqual(self.open_kinds(), ["video_lost"])
        self.run_ird(1)
        self.assertEqual(self.open_kinds(), [])

    def test_log_reset_closes_open_warnings(self):
        self.det.on_logs(1000, "A", [])
        self.det.on_logs(2000, "A", [self.msg(500, "No input activity", "warning")])
        # IRD redémarré : nId repartis de zéro, l'ancien 500 réutilisé par un message permanent.
        changed = self.det.on_logs(3000, "A", [self.msg(3), self.msg(500, "Program is scrambled", "information")])
        self.assertEqual(self.open_kinds(), [])
        self.assertTrue(changed[0]["detail"].endswith("— journal IRD réinitialisé"))

    def test_reception_change_rebaselines_margin(self):
        self.feed(ird(10.5), 60)
        self.feed(ird(7.0), 5)
        self.assertEqual(self.kinds(open_only=True), ["margin_drop"])
        # La liaison montante passe en 8PSK : marge plus basse durablement, ce n'est plus une chute.
        changed = self.feed(replace(ird(7.0), modcod="dvbs2 8psk 3/4"))
        [closed] = [e for e in changed if e["kind"] == "margin_drop"]
        self.assertTrue(closed["detail"].endswith("— réception modifiée (modulation, fréquence ou débit symbole)"))
        self.feed(replace(ird(7.0), modcod="dvbs2 8psk 3/4"), 120)
        self.assertEqual(self.kinds(open_only=True), [])

    def test_missing_reception_field_is_not_a_change(self):
        self.feed(ird(10.5), 60)
        self.feed(ird(7.0), 5)
        self.feed(replace(ird(7.0), modcod=None, symbol_rate=None))
        self.assertEqual(self.kinds(open_only=True), ["margin_drop"])

    def test_retune_rebaselines_margin(self):
        self.feed(replace(ird(10.5), rf_khz=11_220_806), 60)
        self.feed(replace(ird(7.0), rf_khz=11_220_806), 5)
        self.feed(replace(ird(7.0), rf_khz=12_583_023))  # IRD re-réglé sur une autre porteuse
        self.assertEqual(self.kinds(open_only=True), [])
        self.feed(replace(ird(7.0), rf_khz=12_583_100), 120)  # petite dérive : même porteuse, pas de remise à zéro
        self.assertEqual(self.kinds(open_only=True), [])

    def test_margin_low_off_by_default(self):
        self.feed(ird(1.0), 10)
        self.assertNotIn("margin_low", self.kinds())

    def test_margin_low_after_3s_with_hysteresis(self):
        self.det.set_thresholds(0, 2.0, 3.0)
        self.feed(ird(5.0), 5)
        self.feed(ird(2.8), 3)
        self.assertNotIn("margin_low", self.kinds())
        [ev] = [e for e in self.feed(ird(2.8)) if e["kind"] == "margin_low"]
        self.assertEqual((ev["severity"], ev["detail"]), ("warning", "IRD A : marge 2,8 dB sous le minimum de 3,0 dB"))
        self.feed(ird(3.2), 3)  # à moins de 0,5 dB au-dessus : toujours basse
        self.assertIn("margin_low", self.kinds(open_only=True))
        self.feed(ird(3.6))
        self.assertNotIn("margin_low", self.kinds(open_only=True))

    def test_margin_low_closed_by_lock_loss(self):
        self.det.set_thresholds(0, 2.0, 3.0)
        self.feed(ird(2.0), 5)
        self.feed(ird(0.0, locked=False))
        self.assertEqual(self.kinds(open_only=True), ["lock_lost"])

    def test_set_thresholds_applies_to_existing_irds(self):
        self.feed(ird(10.5), 60)
        self.det.set_thresholds(self.t, 4.0, 0.0)
        self.feed(ird(7.0), 5)  # chute de 3,5 dB : sous le nouveau seuil de 4 dB
        self.assertNotIn("margin_drop", self.kinds())
        self.det.set_thresholds(self.t, 2.0, 8.0)
        self.feed(ird(7.0), 4)
        self.assertIn("margin_low", self.kinds(open_only=True))
        changed = self.det.set_thresholds(self.t, 2.0, 0.0)  # minimum désactivé : l'alerte se ferme
        self.assertTrue(any(e["kind"] == "margin_low" and e["detail"].endswith("— seuil modifié") for e in changed))

    def test_device_down_once(self):
        self.det.on_state(1000, "fsh", "down", "délai")
        self.det.on_state(2000, "fsh", "down", "délai")
        self.assertEqual(self.kinds(open_only=True), ["device_down"])
        self.assertEqual(self.store.events(0, 10**12)[0]["detail"], "fsh")
        changed = self.det.on_state(3000, "fsh", "up", "")
        self.assertEqual(changed[0]["ts_end"], 3000)
        self.assertEqual(self.kinds(open_only=True), [])


if __name__ == "__main__":
    unittest.main()
