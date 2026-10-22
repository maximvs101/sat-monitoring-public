import time
import unittest

from monitoring.config import FshConfig
from monitoring.fsh import FshClient, FshError, FshReader, FshSettings, MarkerSample
from tests.fake_scpi import FakeScpi

OK = {"CALC:MARK1:Y?": "-40.764",
      "FREQ:SPAN?": "50000000.000000", "BAND?": "300000.000000"}
# M1 au centre, M4 sur le bruit ; les autres éteints (ils répondent quand même, sans signification).
MULTI = {"FREQ:SPAN?": "50000000.000000", "BAND?": "300000.000000",
         **{f"CALC:MARK{n}?": "1" if n in (1, 4) else "0" for n in range(1, 7)},
         **{f"CALC:MARK{n}:X?": f"11.2{n}e+9" for n in range(1, 7)},
         **{f"CALC:MARK{n}:Y?": f"-{50 + n}.5" for n in range(1, 7)}}


class FshTest(unittest.TestCase):
    def make(self, responses=OK, delay_s=0.0, timeout_s=0.5):
        srv = FakeScpi(responses, delay_s)
        self.addCleanup(srv.stop)
        client = FshClient(FshConfig(host="127.0.0.1", port=srv.port, timeout_s=timeout_s))
        self.addCleanup(client.close)
        return srv, client

    def test_read_level(self):
        srv, c = self.make()
        self.assertEqual(c.read_level(1), -40.764)
        self.assertEqual(srv.received, ["CALC:MARK1:Y?"])

    def test_no_unchecked_marker_read(self):
        # Sur le FSH4 V1.20, lire :X? ou :Y? d'un marqueur éteint le rallume : seul FshReader lit les niveaux,
        # après CALC:MARKn?. Aucune lecture « du marqueur courant » ne doit exister.
        self.assertFalse(hasattr(FshClient, "read_marker"))

    def test_no_answer_raises(self):
        _, c = self.make({**OK, "CALC:MARK1:Y?": None}, timeout_s=0.2)
        with self.assertRaises(FshError):
            c.read_level(1)

    def test_non_numeric_raises(self):
        _, c = self.make({**OK, "CALC:MARK1:Y?": "abc"})
        with self.assertRaises(FshError):
            c.read_level(1)

    def test_peer_closed_raises(self):
        srv, c = self.make()
        c.read_level(1)
        srv.drop_clients()
        time.sleep(0.05)
        with self.assertRaises(FshError):
            c.read_level(1)

    def test_late_reply_not_reused(self):
        # Une réponse arrivée après le délai ne doit pas être prise pour la suivante.
        srv, c = self.make(delay_s=0.3, timeout_s=0.2)
        with self.assertRaises(FshError):
            c.read_level(1)
        srv.delay_s = 0.0
        time.sleep(0.4)  # la réponse tardive arrive sur l'ancienne connexion
        self.assertEqual(c.read_level(1), -40.764)
        self.assertEqual(srv.connections, 2)

    def test_reconnects_after_error(self):
        srv, c = self.make({**OK, "CALC:MARK1:Y?": "abc"})
        with self.assertRaises(FshError):
            c.read_level(1)
        srv.responses["CALC:MARK1:Y?"] = "-41.0"
        self.assertEqual(c.read_level(1), -41.0)
        self.assertEqual(srv.connections, 2)

    def test_read_settings(self):
        srv, c = self.make(MULTI)
        self.assertEqual(c.read_settings(), FshSettings(span_hz=50e6, rbw_hz=300e3, markers=((1, 11.21e9), (4, 11.24e9))))
        self.assertEqual(srv.received[:2], ["FREQ:SPAN?", "BAND?"])

    def test_reader_reads_active_markers(self):
        srv, c = self.make(MULTI)
        now = [100.0]
        reader = FshReader(c, settings_every_s=10.0, clock=lambda: now[0])
        r = reader.read()
        self.assertEqual(r.settings, FshSettings(50e6, 300e3, ((1, 11.21e9), (4, 11.24e9))))
        self.assertEqual(r.markers, {1: MarkerSample(11.21e9, -51.5), 4: MarkerSample(11.24e9, -54.5)})
        # Les marqueurs éteints ne sont jamais lus.
        self.assertNotIn("CALC:MARK2:Y?", srv.received)
        self.assertNotIn("CALC:MARK2:X?", srv.received)

    def test_reader_reads_settings_every_10s(self):
        srv, c = self.make(MULTI)
        now = [100.0]
        reader = FshReader(c, settings_every_s=10.0, clock=lambda: now[0])
        reader.read()
        now[0] = 105.0
        self.assertEqual(reader.read().settings.span_hz, 50e6)  # dernière valeur connue
        now[0] = 110.0
        reader.read()
        self.assertEqual(srv.received.count("BAND?"), 2)
        self.assertEqual(srv.received.count("CALC:MARK2?"), 2)  # éteint : seulement aux relevés complets
        self.assertEqual(srv.received.count("CALC:MARK4?"), 2 + 3)  # allumé : vérifié aussi à chaque lecture
        self.assertEqual(srv.received.count("CALC:MARK1:X?"), 2)
        self.assertEqual(srv.received.count("CALC:MARK4:Y?"), 3)

    def test_marker_turned_off_is_never_read(self):
        # Sur le FSH4 V1.20, lire la position ou le niveau d'un marqueur éteint le rallume.
        srv, c = self.make(MULTI)
        now = [100.0]
        reader = FshReader(c, settings_every_s=10.0, clock=lambda: now[0])
        reader.read()
        srv.responses["CALC:MARK4?"] = "0"  # éteint sur l'appareil entre deux relevés
        now[0] = 101.0
        r = reader.read()
        self.assertEqual(sorted(r.markers), [1])
        self.assertEqual(srv.received.count("CALC:MARK4:Y?"), 1)  # seulement avant l'extinction
        now[0] = 102.0
        r = reader.read()  # relevé complet forcé : les réglages reflètent l'extinction
        self.assertEqual(r.settings.markers, ((1, 11.21e9),))
        self.assertEqual(srv.received.count("CALC:MARK4:Y?"), 1)
        self.assertEqual(srv.received.count("CALC:MARK4:X?"), 1)

    def test_reader_no_active_marker(self):
        _, c = self.make({**MULTI, "CALC:MARK1?": "0", "CALC:MARK4?": "0"})
        r = FshReader(c).read()
        self.assertEqual(r.markers, {})

    def test_close_is_idempotent(self):
        _, c = self.make()
        c.read_level(1)
        c.close()
        c.close()


if __name__ == "__main__":
    unittest.main()
