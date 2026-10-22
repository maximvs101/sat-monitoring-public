import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from monitoring.app import App
from monitoring.config import IrdEntry, PathsConfig, ServerConfig, load_config, save_irds
from monitoring.server import make_server
from tests.test_app import FakeIrds, fsh, sample

READING = fsh({1: (11_220.8e6, -50.0), 4: (11_200e6, -67.0)})


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.web = root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<h1>monitoring</h1>", encoding="utf-8")
        (root / "config.toml").write_text("secret", encoding="utf-8")
        self.cfg_path = root / "conf" / "config.toml"
        self.cfg_path.parent.mkdir()
        self.cfg_path.write_text('[fsh]\nhost = "192.168.10.224" # FSH4\n', encoding="utf-8")
        save_irds(self.cfg_path, (IrdEntry("S1", "IRD 1", "10.0.0.1"),))
        cfg = load_config(self.cfg_path)
        self.cfg = replace(cfg, server=ServerConfig(port=0),
                           paths=PathsConfig(db=str(root / "m.db"), log_dir=str(root / "logs")))
        self.irds = FakeIrds()
        self.closed = 0
        self.app = App(self.cfg, fsh_read=lambda: READING, fsh_close=self._count_close,
                       ird_factory=self.irds.factory, web_dir=self.web, config_path=self.cfg_path,
                       scanner=lambda: {"networks": ["10.0.0.0/24"], "fsh": [], "ird": []})
        self.addCleanup(self.app.stop)
        self.httpd = make_server(self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def _count_close(self):
        self.closed += 1

    def request(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"} if body is not None or raw is not None else {}
        h.update(headers or {})
        c.request(method, path, body=raw if raw is not None else (None if body is None else json.dumps(body)), headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def get(self, path):
        return self.request("GET", path)

    def get_json(self, path):
        status, body = self.get(path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def post(self, path, body=None, headers=None):
        return self.request("POST", path, body, headers)

    def step(self):
        self.app.fsh_loop.step()
        for loop in self.app.ird_loops.values():
            loop.step()

    def raw_response(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    # --- page et nom d'hôte ---

    def test_static_files_are_revalidated(self):
        # Après une mise à jour, le navigateur ne doit pas garder d'anciens scripts en cache.
        r = self.raw_response("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")

    def test_host_by_name_refused(self):
        # Rattachement DNS : un nom de domaine qui pointe vers 127.0.0.1 ne doit rien lire.
        for path in ("/", "/api/status"):
            self.assertEqual(self.raw_response("GET", path, {"Host": f"evil.example:{self.port}"}).status, 403, path)
        self.assertEqual(self.raw_response("POST", "/api/fsh/pause", {"Host": "evil.example"}).status, 403)

    def test_host_localhost_or_ip_accepted(self):
        for host in (f"localhost:{self.port}", f"127.0.0.1:{self.port}", f"[::1]:{self.port}", "192.168.1.20:8080"):
            self.assertEqual(self.raw_response("GET", "/api/status", {"Host": host}).status, 200, host)

    # --- lecture ---

    def test_status(self):
        self.step()
        st = self.get_json("/api/status")
        self.assertEqual(st["irds"]["S1"]["name"], "IRD 1")
        self.assertEqual(st["irds"]["S1"]["latest"]["margin_db"], 6.3)
        self.assertEqual(st["carriers"][0]["key"], "125830-V")
        self.assertEqual(st["fsh"]["host"], "192.168.10.224")
        self.assertEqual(st["markers"]["1"]["level_dbm"], -50.0)

    def test_series_bad_params_400(self):
        for q in ("", "?to=5", "?from=abc&to=5", "?from=10&to=5", "?from=1&to=5&max_points=x"):
            self.assertEqual(self.get("/api/series" + q)[0], 400, q)

    def test_series_has_carriers_for_range(self):
        # Les deux périodes de l'historique : IRD 5 à 11 220,8 MHz (polarisation inconnue), IRD 1 à 12 583,0 MHz V.
        old = replace(sample(rf_khz=11_220_806, pol=None, margin=10.5), symbol_rate=24_760_000, roll_off=0.2)
        for ts in (1000, 2000):
            self.app.store.add_ird(ts, "1000-00002", old)
        self.app.store.set_ird_name("1000-00002", "IRD 5", 2000)
        for ts in (5000, 6000):
            self.app.store.add_ird(ts, "S1", sample())
            self.app.store.add_marker(ts, READING.markers[1], 1)
        s = self.get_json("/api/series?from=0&to=10000")
        self.assertEqual(sorted(s["irds"]), ["1000-00002", "S1"])
        self.assertEqual(s["names"]["1000-00002"], "IRD 5")
        self.assertEqual([c["key"] for c in s["carriers"]], ["112208-?", "125830-V"])
        self.assertEqual(s["carriers"][0]["irds"], ["1000-00002"])
        self.assertEqual(s["fsh"]["1"]["level_dbm"], [-50.0, -50.0])
        self.assertEqual(s["carriers"][0]["markers"], [1])

    def test_series_splits_retuned_ird(self):
        for ts in (1000, 2000):
            self.app.store.add_ird(ts, "S1", sample(rf_khz=11_220_806))
        for ts in (5000, 6000):
            self.app.store.add_ird(ts, "S1", sample())
        s = self.get_json("/api/series?from=0&to=10000")
        by_key = {c["key"]: c for c in s["carriers"]}
        self.assertEqual(sorted(by_key), ["112208-V", "125830-V"])
        self.assertEqual(by_key["112208-V"]["periods"], {"S1": [[1000, 4999]]})
        self.assertEqual(by_key["125830-V"]["periods"], {"S1": [[5000, 6000]]})
        self.assertEqual(by_key["125830-V"]["irds"], ["S1"])

    def test_events_endpoint(self):
        self.app.store.open_event(1000, "lock_lost", "critical", "x", "ird:S1")
        ev = self.get_json("/api/events?from=0&to=5000")
        self.assertEqual([(e["kind"], e["source"]) for e in ev], [("lock_lost", "ird:S1")])
        self.assertEqual(self.get("/api/events?from=a&to=1")[0], 400)

    def test_live_receives_markers(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/live")
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        self.app.fsh_loop.step()
        while True:
            line = r.fp.readline().decode()
            if line.startswith("data:"):
                msg = json.loads(line[5:])
                if msg["type"] == "markers":
                    break
        self.assertEqual(msg["markers"]["1"]["level_dbm"], -50.0)
        c.close()

    def test_live_ird_message_has_health(self):
        from monitoring.ird import IrdHealth
        self.irds.health["S1"] = IrdHealth(48.0, 42.0, False, False, False)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/live")
        r = c.getresponse()
        self.app.ird_loops["S1"].step()
        while True:
            line = r.fp.readline().decode()
            if line.startswith("data:"):
                msg = json.loads(line[5:])
                if msg["type"] == "ird":
                    break
        self.assertEqual(msg["health"]["fpga_temp"], 48.0)
        self.assertIn("video_active", msg)
        self.assertEqual(msg["state"], 0)
        c.close()

    def test_unexpected_error_returns_500(self):
        def boom():
            raise RuntimeError("panne imprévue")
        self.app.status = boom
        status, body = self.get("/api/status")
        self.assertEqual(status, 500)
        self.assertIn("erreur", json.loads(body))

    def test_negative_content_length_rejected(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        c.putrequest("POST", "/api/irds/S1")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", "-1")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 400)  # sans attendre la fermeture du client
        c.close()

    def test_set_thresholds(self):
        from monitoring.config import load_config as load
        self.assertEqual(self.post("/api/detect", {"margin_drop_db": 2.5, "margin_min_db": 3})[0], 204)
        self.assertEqual((load(self.cfg_path).detect.margin_drop_db, load(self.cfg_path).detect.margin_min_db), (2.5, 3.0))
        cfg = self.get_json("/api/status")["config"]
        self.assertEqual((cfg["margin_drop_db"], cfg["margin_min_db"]), (2.5, 3.0))
        self.assertEqual(self.post("/api/detect", {"margin_drop_db": 0, "margin_min_db": 3})[0], 400)
        self.assertEqual(self.post("/api/detect", {"margin_drop_db": 2, "margin_min_db": -1})[0], 400)
        self.assertEqual(self.post("/api/detect", {"margin_drop_db": "x", "margin_min_db": 1})[0], 400)

    def test_get_devices(self):
        self.step()
        d = self.get_json("/api/devices")
        self.assertEqual(d["fsh"], {"host": "192.168.10.224", "port": 5555, "polarization": None})
        self.assertEqual(d["irds"], [{"serial": "S1", "name": "IRD 1", "host": "10.0.0.1", "state": "up",
                                      "carrier": "125830-V", "polarization": None,
                                      "effective_polarization": "vertical"}])

    def test_get_carriers(self):
        self.step()
        [c] = self.get_json("/api/carriers")
        self.assertEqual((c["key"], c["name"], c["irds"]), ("125830-V", "12 583,0 V", ["S1"]))

    # --- FSH4 ---

    def test_set_fsh(self):
        self.assertEqual(self.post("/api/fsh", {"host": "10.0.0.8", "port": 5025, "polarization": "vertical"})[0], 204)
        cfg = load_config(self.cfg_path)
        self.assertEqual((cfg.fsh.host, cfg.fsh.port, cfg.fsh.polarization), ("10.0.0.8", 5025, "vertical"))
        self.assertIn("# FSH4", self.cfg_path.read_text(encoding="utf-8"))

    def test_set_fsh_rejects_bad_input(self):
        for body in ({"host": 'x" ; rm', "port": 5555, "polarization": None},
                     {"host": "10.0.0.8", "port": 70000, "polarization": None},
                     {"host": "10.0.0.8", "port": 5555, "polarization": "oblique"},
                     {"host": "", "port": 5555}):
            self.assertEqual(self.post("/api/fsh", body)[0], 400, body)
        self.assertEqual(load_config(self.cfg_path).fsh.host, "192.168.10.224")

    def test_json_required(self):
        status, _ = self.request("POST", "/api/fsh", headers={"Content-Type": "text/plain"},
                                 raw=json.dumps({"host": "10.0.0.8", "port": 5555}))
        self.assertEqual(status, 415)

    def test_pause_resume(self):
        self.assertEqual(self.post("/api/fsh/pause")[0], 204)
        self.app.fsh_loop.step()
        self.assertEqual(self.app.fsh_loop.state, "paused")
        self.assertEqual(self.closed, 1)
        self.assertEqual(self.post("/api/fsh/resume")[0], 204)
        self.app.fsh_loop.step()
        self.assertEqual(self.app.fsh_loop.state, "up")

    # --- IRD ---

    def test_add_ird(self):
        status, body = self.post("/api/irds", {"host": "10.0.0.2"})
        self.assertEqual(status, 201, body)
        self.assertEqual(json.loads(body), {"serial": "S2", "name": "IRD 2", "host": "10.0.0.2", "polarization": None})
        self.assertIn("S2", self.app.ird_loops)

    def test_add_ird_errors(self):
        self.assertEqual(self.post("/api/irds", {"host": "10.0.0.1"})[0], 409)  # déjà suivi
        status, body = self.post("/api/irds", {"host": "10.0.0.99"})
        self.assertEqual(status, 502)
        self.assertIn("IRD injoignable", json.loads(body)["erreur"])
        self.assertEqual(self.post("/api/irds", {"host": "pas une adresse!"})[0], 400)

    def test_set_ird_polarization(self):
        self.assertEqual(self.post("/api/irds/S1", {"polarization": "horizontal"})[0], 204)
        self.assertEqual(load_config(self.cfg_path).irds[0].polarization, "horizontal")
        self.step()
        d = self.get_json("/api/devices")
        self.assertEqual((d["irds"][0]["polarization"], d["irds"][0]["effective_polarization"]), ("horizontal", "horizontal"))
        self.assertEqual(self.post("/api/irds/S1", {"polarization": None})[0], 204)  # retour à « automatique »
        self.assertIsNone(load_config(self.cfg_path).irds[0].polarization)
        self.assertEqual(self.post("/api/irds/S1", {"polarization": "oblique"})[0], 400)

    def test_update_and_remove_ird(self):
        self.assertEqual(self.post("/api/irds/S1", {"name": "Récepteur A"})[0], 204)
        self.assertEqual(self.app.names["S1"], "Récepteur A")
        self.assertEqual(self.post("/api/irds/S9", {"name": "x"})[0], 404)
        self.assertEqual(self.post("/api/irds/S1", {"host": "pas une adresse!"})[0], 400)
        self.assertEqual(self.post("/api/irds/S1/remove")[0], 204)
        self.assertEqual(self.app.ird_loops, {})
        self.assertEqual(self.post("/api/irds/S1/remove")[0], 404)

    # --- Porteuses ---

    def test_carriers_routes(self):
        self.step()
        self.assertEqual(self.post("/api/carriers/125830-V", {"name": "Client A"})[0], 204)
        self.assertEqual(self.get_json("/api/carriers")[0]["name"], "Client A")
        status, _ = self.post("/api/carriers", {"name": "Sans IRD", "freq_mhz": 11685.0, "polarization": "horizontal",
                                                 "symbol_rate_msps": 27.5})
        self.assertEqual(status, 201)
        self.assertEqual([c["key"] for c in self.get_json("/api/carriers")], ["116850-H", "125830-V"])
        self.assertEqual(self.post("/api/carriers/125830-V/remove")[0], 409)
        self.assertEqual(self.post("/api/carriers/116850-H/remove")[0], 204)
        self.assertEqual(self.post("/api/carriers/999999-V", {"name": "x"})[0], 404)

    def test_add_carrier_rejects_bad_input(self):
        for body in ({"name": "", "freq_mhz": 11685.0}, {"name": "x", "freq_mhz": "abc"},
                     {"name": "x", "freq_mhz": 11685.0, "polarization": "oblique"},
                     {"name": "x", "freq_mhz": 11685.0, "symbol_rate_msps": -1}):
            self.assertEqual(self.post("/api/carriers", body)[0], 400, body)

    # --- Sécurité et divers ---

    def test_foreign_origin_forbidden(self):
        evil = {"Origin": "http://evil.example"}
        for path, body in (("/api/fsh", {"host": "10.0.0.8", "port": 5555}), ("/api/irds", {"host": "10.0.0.2"}),
                           ("/api/irds/S1/remove", None), ("/api/carriers", {"name": "x", "freq_mhz": 11685.0}),
                           ("/api/discover", None), ("/api/fsh/pause", None)):
            self.assertEqual(self.post(path, body, evil)[0], 403, path)
        self.assertIn("S1", self.app.ird_loops)

    def test_discover(self):
        status, body = self.post("/api/discover", None, {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["networks"], ["10.0.0.0/24"])

    def test_static_index(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"monitoring", body)

    def test_static_traversal_404(self):
        for p in ("/../config.toml", "/%2e%2e/config.toml", "/%2E%2E%2Fconfig.toml", "/nope.js"):
            status, body = self.get(p)
            self.assertEqual(status, 404, p)
            self.assertNotIn(b"secret", body)

    def test_static_nul_byte_404(self):
        self.assertEqual(self.get("/%00")[0], 404)
        self.assertEqual(self.get("/a%00b.js")[0], 404)

    def test_unknown_route_404(self):
        self.assertEqual(self.get("/api/nope")[0], 404)
        self.assertEqual(self.get("/api/fsh/pause")[0], 404)
        self.assertEqual(self.post("/api/devices", {})[0], 404)  # ancienne route retirée


class StartupTest(unittest.TestCase):
    def test_startup_message_lists_devices(self):
        from monitoring.config import Config
        from monitoring.server import startup_message
        cfg = replace(Config(), irds=(IrdEntry("S1", "IRD 1", "10.0.0.1"), IrdEntry("", "IRD", "10.0.0.2")))
        msg = startup_message(cfg, 8080)
        self.assertIn("FSH4 192.168.20.222:5555", msg)
        self.assertIn("IRD 1 (10.0.0.1)", msg)
        self.assertIn("IRD (10.0.0.2)", msg)
        self.assertIn("http://127.0.0.1:8080", msg)
        self.assertIn("aucun IRD", startup_message(Config(), 8080))


class PortBusyTest(unittest.TestCase):
    def test_port_already_used_gives_clear_message(self):
        # Outil déjà lancé (ou autre logiciel sur le port) : message clair, page existante ouverte, pas de trace.
        import socket
        from unittest import mock
        from monitoring import server
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cfg = Path(d.name) / "config.toml"
        cfg.write_text(f'[server]\nport = {port}\n[paths]\nlog_dir = "logs"\ndb = "m.db"\n', encoding="utf-8")
        opened = []
        with mock.patch.object(server.webbrowser, "open", opened.append), \
                self.assertLogs("monitoring", "ERROR") as logs, self.assertRaises(SystemExit) as stop:
            server.main(["--config", str(cfg), "--open"])
        self.assertEqual(stop.exception.code, 1)
        self.assertIn(f"port {port} déjà utilisé", "\n".join(logs.output))
        self.assertEqual(opened, [f"http://localhost:{port}"])
        for h in list(__import__("logging").getLogger().handlers):  # libère logs/monitoring.log (Windows)
            h.close()
            __import__("logging").getLogger().removeHandler(h)


class SignalTest(unittest.TestCase):
    def test_stop_signals_stop_cleanly(self):
        import signal
        from monitoring.server import install_signal_handlers
        # SIGHUP (fenêtre Terminal fermée) n'existe pas sous Windows, SIGBREAK (Ctrl-Pause) seulement là.
        sigs = [getattr(signal, n) for n in ("SIGHUP", "SIGTERM", "SIGBREAK") if hasattr(signal, n)]
        saved = {sig: signal.getsignal(sig) for sig in sigs}
        self.addCleanup(lambda: [signal.signal(sig, h) for sig, h in saved.items()])
        install_signal_handlers()
        for sig in saved:
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(sig)(sig, None)

    def test_signal_handlers_without_sighup(self):
        # Comme sous Windows : pas de SIGHUP dans le module signal.
        import types
        from monitoring import server
        calls = []
        fake = types.SimpleNamespace(SIGTERM=15, signal=lambda sig, h: calls.append(sig))
        server.install_signal_handlers(fake)
        self.assertEqual(calls, [15])


if __name__ == "__main__":
    unittest.main()
