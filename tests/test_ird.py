import os
import threading
import time
import unittest
from unittest import mock
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from monitoring.config import IrdConfig
from monitoring.ird import (THUMB_MAX_BYTES, AudioVoice, IrdBusy, IrdClient, IrdError, IrdHealth, IrdLogMessage,
                            IrdReader, IrdReading, IrdSample, band_state, parse_health, parse_logs, parse_refresh, parse_status)

FIXTURE = (Path(__file__).parent / "fixtures" / "ird_refresh.xml").read_bytes()
PRESETS = (Path(__file__).parent / "fixtures" / "ird_refresh_presets.xml").read_bytes()
DECODE = (Path(__file__).parent / "fixtures" / "ird_refresh_decode.xml").read_bytes()
STATUS = (Path(__file__).parent / "fixtures" / "ird_status.xml").read_bytes()
LOGS = (Path(__file__).parent / "fixtures" / "ird_logs.xml").read_bytes()
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100 + b"\xff\xd9"


def status_xml(serial="1000-00001", name="IRD 1", vendor="Ateme"):
    return (f"<response command='status' status='ok'><system><id>{serial}</id><systemName>{name}</systemName>"
            f"<vendorName>{vendor}</vendorName><modelName>DR5000</modelName></system></response>").encode()


class ParseTest(unittest.TestCase):
    def test_parse_fixture(self):
        # Valeurs capturées sur l'IRD 5 le 17/10/2026.
        s = parse_refresh(FIXTURE)
        self.assertIsInstance(s, IrdSample)
        self.assertIs(s.locked, True)
        self.assertIs(s.ts_locked, True)
        self.assertAlmostEqual(s.cn_db, 15.7, delta=0.05)
        self.assertAlmostEqual(s.margin_db, 10.5, delta=0.05)
        self.assertEqual(s.power_dbm, -38)
        self.assertEqual(s.if_khz, 1470806)
        self.assertEqual(s.symbol_rate, 24760000)
        self.assertEqual(s.modcod, "dvbs2 qpsk 5/6")
        self.assertEqual(s.ber, 0.0)
        self.assertEqual(s.ber_raw, "0")
        self.assertGreater(s.bitrate_kbps, 0)

    def test_ber_scaled_like_the_ird_page(self):
        # L'interface du DR5000 affiche ber / 1e8 (« < 1e-8 » quand il vaut 0).
        for raw, expected in (("100", 1e-6), ("1", 1e-8), ("2500000", 0.025), ("0", 0.0)):
            s = parse_refresh(FIXTURE.replace(b"<ber>0</ber>", f"<ber>{raw}</ber>".encode(), 1))
            self.assertAlmostEqual(s.ber, expected, msg=raw)
            self.assertEqual(s.ber_raw, raw)

    def test_unlocked(self):
        data = FIXTURE.replace(b"<locked>true</locked>", b"<locked>false</locked>", 1)
        self.assertIs(parse_refresh(data).locked, False)

    def test_missing_field_is_none(self):
        data = FIXTURE.replace(b"<snrMargin>105</snrMargin>", b"")
        self.assertIsNone(parse_refresh(data).margin_db)

    def test_invalid_xml_raises(self):
        with self.assertRaises(IrdError):
            parse_refresh(b"<resp")

    def test_error_status_raises(self):
        with self.assertRaises(IrdError):
            parse_refresh(b"<response status='error'><message>Unknown command</message></response>")

    def test_missing_input_raises(self):
        with self.assertRaises(IrdError):
            parse_refresh(b"<response status='ok'><probe/></response>")


class FakeIrd:
    """Faux DR5000 : répond à <status/> avec `serial` et à <refresh/> avec `refresh`."""

    def __init__(self, delay_s=0.0, status=200, refresh=FIXTURE, serial="1000-00001", extras_status=200, thumb=JPEG,
                 thumb_status=200, thumb_delay_s=0.0, thumb_chunk_s=0.0):
        outer = self
        self.bodies = []
        self.gets = []
        self.thumb = thumb
        self.thumb_status = thumb_status
        self.serial = serial
        self.refresh = refresh
        self.status = status
        self.extras_status = extras_status
        self.identified = False

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                outer.bodies.append((self.headers.get("Content-Type"), body))
                time.sleep(delay_s)
                extra = b"logs" in body or (b"status" in body and outer.identified)
                if b"status" in body:
                    outer.identified = True
                code = outer.extras_status if extra else outer.status
                self.send_response(code)
                self.end_headers()
                if b"logs" in body:
                    self.wfile.write(LOGS)
                elif b"status" in body:
                    self.wfile.write(STATUS.replace(b"1000-00001", outer.serial.encode()))
                else:
                    self.wfile.write(outer.refresh)

            def do_GET(self):
                outer.gets.append(self.path)
                if not self.path.startswith("/thumbnail.jpg"):
                    self.send_response(404)
                    self.end_headers()
                    return
                time.sleep(thumb_delay_s)
                self.send_response(outer.thumb_status)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                if outer.thumb_status != 200:
                    return
                if not thumb_chunk_s:
                    self.wfile.write(outer.thumb)
                    return
                for i in range(0, len(outer.thumb), 10):  # au compte-gouttes : chaque morceau arrive avant le délai
                    self.wfile.write(outer.thumb[i:i + 10])
                    self.wfile.flush()
                    time.sleep(thumb_chunk_s)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def refresh_count(self):
        return sum(1 for _, b in self.bodies if b == b"xml=<refresh/>")

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ClientTest(unittest.TestCase):
    def test_fetch_against_local_http(self):
        srv = FakeIrd()
        self.addCleanup(srv.stop)
        s = IrdClient(srv.host, IrdConfig()).fetch()
        self.assertIsInstance(s, IrdSample)
        ctype, body = srv.bodies[0]
        self.assertEqual(body, b"xml=<refresh/>")
        self.assertEqual(ctype, "text/plain; charset=utf-8")

    def test_thumbnail_returns_jpeg(self):
        srv = FakeIrd()
        self.addCleanup(srv.stop)
        self.assertEqual(IrdClient(srv.host, IrdConfig()).thumbnail(), JPEG)
        self.assertTrue(srv.gets[0].startswith("/thumbnail.jpg?forceRefresh="), srv.gets)

    def test_thumbnail_rejects_non_jpeg(self):
        srv = FakeIrd(thumb=b"<html>erreur</html>")
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig()).thumbnail()

    def test_thumbnail_rejects_oversized(self):
        srv = FakeIrd(thumb=b"\xff\xd8" + b"x" * THUMB_MAX_BYTES)
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig()).thumbnail()

    def test_thumbnail_rejects_empty(self):
        srv = FakeIrd(thumb=b"")
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig()).thumbnail()

    def test_thumbnail_rejects_truncated(self):
        # Connexion coupée en cours d'envoi : début de JPEG sans marqueur de fin.
        srv = FakeIrd(thumb=JPEG[:-2])
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig()).thumbnail()

    def test_thumbnail_whole_request_time_bounded(self):
        # Chaque morceau arrive à temps, mais l'ensemble dépasserait 1 s : abandon, la lecture de l'IRD n'attend pas.
        srv = FakeIrd(thumb=JPEG, thumb_chunk_s=0.2)
        self.addCleanup(srv.stop)
        t0 = time.monotonic()
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig()).thumbnail()
        self.assertLess(time.monotonic() - t0, 1.6)

    def test_thumbnail_busy(self):
        srv = FakeIrd(thumb_status=503)
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdBusy):
            IrdClient(srv.host, IrdConfig()).thumbnail()

    def test_fetch_ignores_system_proxy(self):
        # PC d'entreprise avec proxy : les IRD du réseau local ne doivent jamais passer par lui.
        srv = FakeIrd()
        self.addCleanup(srv.stop)
        # urllib ne contourne le proxy que pour la boucle locale : le faux IRD est traité comme une adresse du réseau.
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                                          "no_proxy": "", "NO_PROXY": ""}), \
                mock.patch("urllib.request.proxy_bypass", lambda host: False):
            self.assertIsInstance(IrdClient(srv.host, IrdConfig()).fetch(), IrdSample)

    def test_slow_body_is_not_unreachable(self):
        # L'IRD répond (en-têtes) puis traîne sur le contenu : il n'est pas « injoignable », il est trop lent.
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.send_header("Content-Length", "100000")
                self.end_headers()
                self.wfile.write(b"<response")
                self.wfile.flush()
                time.sleep(1.5)

            def log_message(self, *a):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with self.assertRaises(IrdError) as ctx:
            IrdClient(f"127.0.0.1:{httpd.server_port}", IrdConfig(timeout_s=0.5)).fetch()
        self.assertNotIn("injoignable", str(ctx.exception))
        self.assertIn("trop lente", str(ctx.exception))

    def test_fetch_timeout_raises(self):
        srv = FakeIrd(delay_s=0.5)
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdError):
            IrdClient(srv.host, IrdConfig(timeout_s=0.1)).fetch()

    def test_fetch_503_is_busy(self):
        from monitoring.ird import IrdBusy
        srv = FakeIrd(status=503)
        self.addCleanup(srv.stop)
        with self.assertRaises(IrdBusy):
            IrdClient(srv.host, IrdConfig()).fetch()

    def test_fetch_unreachable_raises(self):
        with self.assertRaises(IrdError):
            IrdClient("127.0.0.1:9", IrdConfig(timeout_s=0.5)).fetch()


class TuningTest(unittest.TestCase):
    def test_rf_from_if_plus_lo(self):
        s = parse_refresh(PRESETS)
        self.assertEqual(s.rf_khz, 12583023)
        self.assertIsNone(s.polarization)  # l'IRD n'alimente pas le LNB : son réglage ne dit rien
        self.assertEqual(s.roll_off, 0.25)
        self.assertEqual(s.symbol_rate, 7200000)

    def test_polarization_only_when_ird_powers_lnb(self):
        powered = PRESETS.replace(b"<isPowered>false</isPowered>", b"<isPowered>true</isPowered>", 1)
        self.assertEqual(parse_refresh(powered).polarization, "vertical")

    def test_rf_falls_back_to_downlink_when_unlocked(self):
        data = (PRESETS.replace(b"<locked>true</locked>", b"<locked>false</locked>", 1)
                .replace(b"<frequency>1983023</frequency>", b"<frequency>0</frequency>", 1))
        self.assertEqual(parse_refresh(data).rf_khz, 12583060)
        self.assertIs(parse_refresh(data).rf_measured, False)
        self.assertIs(parse_refresh(PRESETS).rf_measured, True)

    def test_direct_l_band_input_is_measured(self):
        # Entrée en bande L directe (pas de LNB, oscillateur à 0) et IRD verrouillé : la fréquence est mesurée.
        import re
        data = re.sub(rb"<oscillatorFrequency>\d+</oscillatorFrequency>", b"<oscillatorFrequency>0</oscillatorFrequency>", PRESETS)
        s = parse_refresh(data)
        self.assertEqual((s.rf_khz, s.rf_measured), (s.if_khz, True))

    def test_old_fixture_has_no_rf(self):
        s = parse_refresh(FIXTURE)
        self.assertIsNone(s.rf_khz)
        self.assertIsNone(s.polarization)

    def test_parse_status(self):
        self.assertEqual(parse_status(status_xml()), ("1000-00001", "IRD 1"))
        with self.assertRaises(IrdError):
            parse_status(status_xml(vendor="Autre"))
        with self.assertRaises(IrdError):
            parse_status(b"<response status='ok'><system><vendorName>Ateme</vendorName></system></response>")


class DecodeTest(unittest.TestCase):
    def test_decode_fields(self):
        s = parse_refresh(DECODE)
        self.assertIs(s.video_active, True)
        self.assertGreater(s.video_bitrate, 0)
        self.assertEqual(s.video_format, "hd_1920x1080i_50")
        self.assertEqual((s.audio_configured, s.audio_active, s.audio_bitrate), (5, 5, 384000))
        self.assertEqual((s.scrambled, s.descrambled), (True, True))
        self.assertEqual((s.program_number, s.service_name), (1, "P100/2/CHN/FEED_A1/MAIN"))

    def test_details_video_audio_outputs(self):
        d = parse_refresh(DECODE).details
        self.assertEqual((d.video_codec, d.video_profile, d.video_level), ("h264", "h264_high", "h264_4"))
        self.assertEqual((d.video_width, d.video_height, d.video_fps, d.video_dar, d.video_pid),
                         (1920, 1080, 25.0, "16_9", 512))
        self.assertEqual([v.slot for v in d.audio], [0, 1, 2, 8, 9])  # voies configurées seulement
        self.assertEqual(d.audio[0], AudioVoice(0, 4112, "mpa", 2, 48000, 384000, True))
        self.assertEqual(d.sdi_formats, ("hd_1920x1080i_50", "hd_1920x1080i_50"))
        self.assertEqual((d.asi_descrambled, d.ip_streams, d.genlock), (True, 0, False))
        self.assertEqual((d.loss_pattern, d.loss_freeze_ms), ("blue", 1000))

    # Réponse d'un IRD déverrouillé, relevée sur l'IRD 3 le 22/10/2026 : l'IRD met puissance, débit symbole et
    # fréquence à 0, la modulation à « automatic unknown unknown », et garde les dernières valeurs du décodeur.
    UNLOCKED = (DECODE.replace(b"<locked>true</locked>", b"<locked>false</locked>", 1)
                .replace(b"<snr>152</snr>", b"<snr>0</snr>").replace(b"<snrMargin>73</snrMargin>", b"<snrMargin>0</snrMargin>")
                .replace(b"<power>-41</power>", b"<power>0</power>").replace(b"<frequency>1983022</frequency>", b"<frequency>0</frequency>")
                .replace(b"<symbolRate>7200000</symbolRate>", b"<symbolRate>0</symbolRate>")
                .replace(b"<modulation>8psk</modulation>", b"<modulation>unknown</modulation>")
                .replace(b"<fec>3_4</fec>", b"<fec>unknown</fec>").replace(b"<mode>dvbs2</mode>", b"<mode>automatic</mode>")
                .replace(b"<rollOff>0_25</rollOff>", b"<rollOff>0_35</rollOff>")
                .replace(b"<tsLocked>true</tsLocked>", b"<tsLocked>false</tsLocked>")
                .replace(b"<bitrate>15677</bitrate>", b"<bitrate>0</bitrate>", 1))

    def test_unlocked_has_no_reception_values(self):
        # 0 dBm, 0 MS/s et « automatic unknown unknown » ne sont pas des mesures : rien plutôt que des valeurs fausses.
        s = parse_refresh(self.UNLOCKED)
        self.assertIs(s.locked, False)
        self.assertIsNone(s.power_dbm)
        self.assertIsNone(s.symbol_rate)
        self.assertIsNone(s.modcod)
        self.assertIsNone(s.roll_off)
        self.assertEqual((s.margin_db, s.cn_db), (0.0, 0.0))  # 0 dB = décrochage, voulu
        self.assertEqual((s.ber, s.ber_raw), (None, None))  # 0 n'est pas « < 1e-8 » : pas de mesure

    def test_unlocked_has_no_stale_decoding(self):
        # Sans flux transport, voies audio « actives » et débits vidéo sont ceux d'avant la coupure.
        s = parse_refresh(self.UNLOCKED)
        self.assertEqual((s.audio_active, s.audio_bitrate, s.video_bitrate), (None, None, None))
        self.assertEqual(s.audio_configured, 5)
        d = s.details
        self.assertEqual((d.video_codec, d.video_pid, d.audio), (None, None, ()))
        self.assertEqual(d.sdi_formats, ("hd_1920x1080i_50", "hd_1920x1080i_50"))  # configuration des sorties gardée
        self.assertEqual(band_state(s), 3)

    def test_odd_audio_voice_id_does_not_fail_the_reading(self):
        # Autre firmware : un identifiant de voie non numérique ne doit pas rendre l'IRD « injoignable ».
        data = DECODE.replace(b'<audio id="0">', b'<audio id="main">', 1)
        d = parse_refresh(data).details
        self.assertEqual(d.audio[0].slot, -1)

    def test_out_of_range_numbers_are_dropped(self):
        # Pendant un reverrouillage (IRD 1 et 3, 24/10/2026 16:37), l'IRD a envoyé un nombre géant (« -1 » non signé
        # sur 64 bits, par exemple) : il ne peut pas être enregistré (SQLite : 64 bits signés) et n'a pas de sens.
        huge = b"18446744073709551615"
        data = DECODE.replace(b"<bitrate>15677</bitrate>", b"<bitrate>" + huge + b"</bitrate>", 1)
        s = parse_refresh(data)
        self.assertIsNone(s.bitrate_kbps)

    def test_details_absent_on_old_fixture(self):
        self.assertIsNone(parse_refresh(FIXTURE).details)

    def test_band_state(self):
        ok = parse_refresh(DECODE)
        self.assertEqual(band_state(ok), 0)
        self.assertEqual(band_state(replace(ok, locked=False)), 3)
        self.assertEqual(band_state(replace(ok, ts_locked=False)), 3)
        self.assertEqual(band_state(replace(ok, video_active=False)), 2)
        self.assertEqual(band_state(replace(ok, descrambled=False)), 2)
        self.assertEqual(band_state(replace(ok, bitrate_kbps=0)), 2)
        self.assertEqual(band_state(replace(ok, ber=1e-6)), 1)
        self.assertEqual(band_state(replace(ok, audio_active=0)), 1)
        self.assertEqual(band_state(replace(ok, audio_active=0, video_active=False)), 2)

    def test_old_fixture_decode_unknown(self):
        s = parse_refresh(FIXTURE)
        self.assertEqual((s.video_active, s.audio_configured, s.scrambled, s.program_number), (None, None, None, None))
        self.assertEqual(band_state(s), 0)

    def test_parse_health(self):
        self.assertEqual(parse_health(STATUS), IrdHealth(48.0, 42.0, False, False, False))
        bad = STATUS.replace(b"<overheat>false</overheat>", b"<overheat>true</overheat>")
        self.assertIs(parse_health(bad).overheat, True)

    def test_parse_logs(self):
        self.assertEqual(parse_logs(LOGS), [IrdLogMessage(335, "Program is scrambled", "information", False)])
        ended = LOGS.replace(b"<nTimeEnd>-1</nTimeEnd>", b"<nTimeEnd>1792661047000000</nTimeEnd>")
        self.assertIs(parse_logs(ended)[0].ended, True)


class ReaderTest(unittest.TestCase):
    def test_identify(self):
        srv = FakeIrd()
        self.addCleanup(srv.stop)
        self.assertEqual(IrdClient(srv.host, IrdConfig()).identify(), ("1000-00001", "IRD 1"))

    def test_reader_returns_extras_every_10s(self):
        srv = FakeIrd(refresh=DECODE)
        self.addCleanup(srv.stop)
        now = [100.0]
        reader = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001", clock=lambda: now[0])
        r = reader.read()
        self.assertIsInstance(r, IrdReading)
        self.assertEqual(r.health, IrdHealth(48.0, 42.0, False, False, False))
        self.assertEqual(r.logs, [IrdLogMessage(335, "Program is scrambled", "information", False)])
        now[0] = 105.0
        r = reader.read()
        self.assertIsNone(r.health)
        self.assertIsNone(r.logs)
        now[0] = 110.0
        self.assertIsNotNone(reader.read().health)

    def test_reader_returns_thumb_every_2_5s(self):
        srv = FakeIrd(refresh=DECODE)
        self.addCleanup(srv.stop)
        now = [100.0]
        reader = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001", clock=lambda: now[0])
        self.assertEqual(reader.read().thumb, JPEG)
        now[0] = 101.0
        self.assertIsNone(reader.read().thumb)
        now[0] = 102.5
        self.assertEqual(reader.read().thumb, JPEG)

    def test_reader_thumb_busy_keeps_sample(self):
        srv = FakeIrd(refresh=DECODE, thumb_status=503)
        self.addCleanup(srv.stop)
        r = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001").read()
        self.assertEqual(r.sample.program_number, 1)
        self.assertIsNone(r.thumb)

    def test_reader_thumb_timeout_keeps_sample(self):
        srv = FakeIrd(refresh=DECODE, thumb_delay_s=1.5)
        self.addCleanup(srv.stop)
        r = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001").read()
        self.assertEqual(r.sample.program_number, 1)
        self.assertIsNone(r.thumb)

    def test_reader_thumb_failure_keeps_sample(self):
        srv = FakeIrd(refresh=DECODE, thumb_status=500)
        self.addCleanup(srv.stop)
        r = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001").read()
        self.assertEqual(r.sample.program_number, 1)
        self.assertIsNone(r.thumb)

    def test_reader_extras_failure_keeps_sample(self):
        srv = FakeIrd(refresh=DECODE, extras_status=500)
        self.addCleanup(srv.stop)
        r = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001").read()
        self.assertEqual(r.sample.program_number, 1)
        self.assertIsNone(r.health)
        self.assertIsNone(r.logs)

    def test_reader_extras_failure_logged_once_then_recovery(self):
        # Requêtes annexes en échec toutes les 10 s : une ligne au premier échec, une au retour, pas entre-temps.
        srv = FakeIrd(refresh=DECODE, extras_status=500)
        self.addCleanup(srv.stop)
        clock = [0.0]
        reader = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001", clock=lambda: clock[0])
        with self.assertLogs("monitoring.ird", "INFO") as logs:
            for _ in range(3):
                reader.read()
                clock[0] += 10
            srv.extras_status = 200
            reader.read()
        # Une ligne par requête annexe (état, journal), pas une par relevé.
        self.assertEqual(len([m for m in logs.output if "en échec" in m]), 2)
        self.assertEqual(len([m for m in logs.output if "de nouveau" in m]), 2)

    def test_reader_reads_when_serial_matches(self):
        srv = FakeIrd(refresh=PRESETS)
        self.addCleanup(srv.stop)
        reader = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001")
        self.assertEqual(reader.read().sample.rf_khz, 12583023)
        reader.read()
        # Identification une fois, puis l'état matériel relevé à la première lecture seulement (10 s).
        self.assertEqual(sum(1 for _, b in srv.bodies if b"status" in b), 2)

    def test_reader_rejects_other_serial(self):
        srv = FakeIrd(serial="1000-00002")
        self.addCleanup(srv.stop)
        reader = IrdReader(IrdClient(srv.host, IrdConfig()), "1000-00001")
        with self.assertRaises(IrdError) as ctx:
            reader.read()
        self.assertIn("autre appareil", str(ctx.exception))
        self.assertIn("1000-00002", str(ctx.exception))
        self.assertEqual(srv.refresh_count(), 0)

    def test_reader_rechecks_after_failure(self):
        srv = FakeIrd()
        self.addCleanup(srv.stop)
        reader = IrdReader(IrdClient(srv.host, IrdConfig(timeout_s=0.5)), "1000-00001")
        reader.read()
        srv.status = 500
        with self.assertRaises(IrdError):
            reader.read()
        srv.status = 200
        srv.serial = "1000-00002"
        with self.assertRaises(IrdError) as ctx:
            reader.read()
        self.assertIn("autre appareil", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
