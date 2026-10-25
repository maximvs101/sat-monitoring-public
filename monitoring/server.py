"""Point d'entrée : collecte FSH4 + IRD, API JSON, flux direct (SSE) et page web."""

import argparse
import json
import logging
import sys
import webbrowser
import ipaddress
import mimetypes
import queue
import re
import signal
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import system
from .app import App, carrier_view
from .carriers import Tuning, attach_markers, group_carriers
from .config import POLARIZATIONS, CarrierEntry, check_freq_mhz, load_config
from .ird import IrdError

log = logging.getLogger("monitoring")

PING_S = 15


HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def valid_host(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOSTNAME.match(value))


def _number(value, name, positive=True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (positive and value <= 0):
        raise ValueError(f"{name} invalide")
    return float(value)


def _polarization(value):
    if value not in (None, *POLARIZATIONS):
        raise ValueError("polarisation invalide (vertical, horizontal ou vide)")
    return value


def parse_fsh(body: dict) -> tuple:
    host, port = body.get("host"), body.get("port")
    if host != "" and not valid_host(host):  # vide : pas de FSH4
        raise ValueError("adresse du FSH4 invalide")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port du FSH4 invalide")
    return host, port, _polarization(body.get("polarization"))


REQUEST_TIMEOUT_S = 30


def _name(value, message) -> str:
    """Nom d'IRD ou de porteuse, sans espaces autour ; vide refusé."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value.strip()


def parse_carrier(body: dict) -> CarrierEntry:
    sr = body.get("symbol_rate_msps")
    freq = check_freq_mhz(_number(body.get("freq_mhz"), "fréquence"))
    rate = None if sr is None else _number(sr, "débit symbole")
    if rate is not None and rate <= 0:
        raise ValueError("débit symbole : nombre positif de MS/s attendu")
    return CarrierEntry(_name(body.get("name"), "nom de porteuse manquant"), freq, _polarization(body.get("polarization")),
                        rate, True)


def parse_thresholds(body: dict) -> tuple:
    """Seuils de marge : chute tolérée (0,5 à 20 dB) et minimum fixe (0 = désactivé, jusqu'à 30 dB)."""
    drop = _number(body.get("margin_drop_db"), "chute de marge")
    minimum = _number(body.get("margin_min_db"), "marge minimale", positive=False)
    if not 0.5 <= drop <= 20:
        raise ValueError("chute de marge : entre 0,5 et 20 dB")
    if not 0 <= minimum <= 30:
        raise ValueError("marge minimale : entre 0 et 30 dB (0 = désactivée)")
    return drop, minimum


def series_carriers(app: App, fsh: dict, irds: dict) -> tuple:
    """Porteuses présentes dans une plage d'historique, avec leurs marqueurs."""
    # Chaque période de réglage d'un IRD est rattachée séparément : un IRD re-réglé en cours de plage
    # apparaît sous chacune de ses porteuses, pour la période correspondante.
    tunings, periods = {}, {}
    for serial, v in irds.items():
        for i, seg in enumerate(v.get("segments") or []):
            pid = f"{serial}#{i}"
            tunings[pid] = Tuning(seg["rf_khz"], seg["polarization"], seg["symbol_rate"], seg["roll_off"])
            periods[pid] = (serial, [seg["from"], seg["to"]])
    carriers = group_carriers(tunings, app.cfg.carriers)
    freqs = {n: s["freq_hz"][-1] for n, s in fsh.items() if s["freq_hz"] and s["freq_hz"][-1] is not None}
    mm = attach_markers(carriers, freqs, app.cfg.fsh.polarization, app.cfg.detect.noise_markers)
    out = []
    for c in carriers:
        by_serial = {}
        for pid in c.irds:
            serial, span = periods[pid]
            by_serial.setdefault(serial, []).append(span)
        # Ordre des IRD suivis (comme en Direct), puis les anciens IRD.
        followed = list(app.ird_loops)
        irds_order = sorted(by_serial, key=lambda serial: (followed.index(serial) if serial in followed else len(followed),
                                                          serial))
        out.append({**carrier_view(c, mm, freqs), "irds": irds_order, "periods": by_serial})
    return out, mm


def _int_param(params, name, default=None):
    values = params.get(name)
    if not values:
        if default is None:
            raise ValueError(f"paramètre {name} manquant")
        return default
    value = int(values[0])
    if not -2**63 <= value < 2**63:  # au-delà, SQLite refuse le nombre (erreur 500 sinon)
        raise ValueError(f"paramètre {name} hors limites")
    return value


def make_server(app: App) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_S  # un client qui n'envoie pas toute sa requête ne bloque pas un fil pour toujours
        server_version = "monitoring-sat"

        def log_message(self, fmt, *args):
            log.debug("HTTP %s", fmt % args)

        def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=()):
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            if body or status != 204:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj).encode())

        def _error(self, status, message):
            self._json({"erreur": message}, status)

        def _range(self, params):
            from_ts, to_ts = _int_param(params, "from"), _int_param(params, "to")
            if from_ts > to_ts:
                raise ValueError("from > to")
            return from_ts, to_ts

        def _host_allowed(self) -> bool:
            # Rattachement DNS : une page d'un autre site dont le nom pointe vers cette machine ne doit rien lire.
            # Seuls « localhost » et les adresses IP (consultation depuis le réseau) sont acceptés.
            host = (self.headers.get("Host") or "").strip()
            name = host[1:host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
            if name.lower() == "localhost":
                return True
            try:
                ipaddress.ip_address(name)
                return True
            except ValueError:
                return False

        def _guarded(self, handler):
            """Une erreur imprévue renvoie une 500 lisible par la page, au lieu de couper la connexion."""
            try:
                if not self._host_allowed():
                    return self._error(403, "nom d'hôte refusé")
                return handler()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client parti (page fermée, flux direct interrompu)
            except Exception as e:  # noqa: BLE001
                log.exception("Erreur sur %s %s", self.command, self.path)
                try:
                    self._error(500, f"erreur interne : {e}")
                except OSError:
                    pass

        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _get(self):
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            try:
                if url.path == "/api/status":
                    return self._json(app.status())
                if url.path == "/api/series":
                    from_ts, to_ts = self._range(params)
                    cap = app.cfg.server.max_points
                    max_points = max(1, min(_int_param(params, "max_points", cap), cap))
                    fsh = app.store.marker_series(from_ts, to_ts, max_points)
                    irds = app.store.ird_series(from_ts, to_ts, max_points)
                    carriers, mm = series_carriers(app, fsh, irds)
                    return self._json({"fsh": fsh, "irds": irds, "names": app.store.ird_names(),
                                       "carriers": carriers, "noise": list(mm.noise),
                                       "if_warning": mm.if_warning})
                if url.path == "/api/devices":
                    return self._json(app.devices())
                if url.path == "/api/carriers":
                    return self._json(app.carriers_view())
                if url.path == "/api/events":
                    return self._json(app.store.events(*self._range(params)))
            except ValueError as e:
                return self._error(400, str(e))
            if url.path == "/api/live":
                return self._live()
            if url.path.startswith("/api/ird/") and url.path.endswith("/image.jpg"):
                image = app.thumbnail(unquote(url.path[len("/api/ird/"):-len("/image.jpg")]))
                if image is None:
                    return self._error(404, "pas d'image")
                return self._send(200, image, "image/jpeg", headers=(("Cache-Control", "no-store"),))
            if url.path.startswith("/api/"):
                return self._error(404, "inconnu")
            return self._static(url.path)

        def _same_origin(self) -> bool:
            # Une page d'un autre site ouverte dans le navigateur ne doit pas piloter le service.
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            port = self.server.server_address[1]
            return origin in (f"http://localhost:{port}", f"http://127.0.0.1:{port}", f"http://[::1]:{port}")

        def _read_json(self):
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return None
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                raise ValueError("longueur de requête invalide")  # sinon read(-1) attendrait la fermeture du client
            if length > 10_000:
                raise ValueError("requête trop grande")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("objet JSON attendu")
            return body

        def _post(self):
            path = urlsplit(self.path).path
            if not self._same_origin():
                return self._error(403, "origine refusée")
            if path == "/api/discover":
                return self._json(app.scanner())
            if path in ("/api/fsh/pause", "/api/fsh/resume") and app.fsh_absent:
                return self._error(400, "aucun FSH4 configuré (Réglages › Appareils)")
            if path == "/api/fsh/pause":
                app.fsh_loop.pause()
                return self._send(204)
            if path == "/api/fsh/resume":
                app.fsh_loop.resume()
                return self._send(204)
            parts = [unquote(p) for p in path.split("/")[1:]]  # ["api", "irds", "<série>", "remove"]
            if parts[0:1] == ["api"] and parts[1:2] in (["fsh"], ["irds"], ["carriers"], ["detect"]):
                return self._manage(parts[1], parts[2:])
            return self._error(404, "inconnu")

        def _manage(self, kind: str, rest: list):
            """Réglages du FSH4, des IRD, des porteuses et des seuils (POST), enregistrés dans config.toml."""
            removing = rest[1:] == ["remove"]
            try:
                body = {} if removing else self._read_json()
                if body is None:
                    return self._error(415, "JSON attendu")
                if kind == "detect" and not rest:
                    app.set_thresholds(*parse_thresholds(body))
                    return self._send(204)
                if kind == "fsh" and not rest:
                    app.set_fsh(*parse_fsh(body))
                    return self._send(204)
                if kind == "irds" and not rest:
                    host = body.get("host")
                    if not valid_host(host):
                        raise ValueError("adresse de l'IRD invalide")
                    try:
                        entry = app.add_ird(host)
                    except IrdError as e:
                        return self._error(502, f"IRD non ajouté : {e}")
                    except ValueError as e:
                        return self._error(409, str(e))
                    return self._json(asdict(entry), 201)
                if kind == "irds" and len(rest) == 1:
                    name, host = body.get("name"), body.get("host")
                    if host is not None and not valid_host(host):
                        raise ValueError("adresse de l'IRD invalide")
                    name = None if name is None else _name(name, "nom d'IRD invalide")
                    extra = {"polarization": _polarization(body["polarization"])} if "polarization" in body else {}
                    app.update_ird(rest[0], name, host, **extra)
                    return self._send(204)
                if kind == "irds" and removing:
                    app.remove_ird(rest[0])
                    return self._send(204)
                if kind == "carriers" and not rest:
                    entry = parse_carrier(body)
                    try:
                        app.add_carrier(entry)
                    except ValueError as e:
                        return self._error(409, str(e))
                    return self._send(201, b"{}")
                if kind == "carriers" and len(rest) == 1:
                    app.set_carrier_name(rest[0], _name(body.get("name"), "nom de porteuse manquant"))
                    return self._send(204)
                if kind == "carriers" and removing:
                    try:
                        app.remove_carrier(rest[0])
                    except ValueError as e:
                        return self._error(409, str(e))
                    return self._send(204)
            except KeyError:
                return self._error(404, "introuvable")
            except ValueError as e:
                return self._error(400, str(e))
            except OSError as e:
                log.exception("Échec de l'enregistrement de config.toml")
                return self._error(500, f"config.toml non enregistré : {e}")
            return self._error(404, "inconnu")

        def _static(self, path):
            rel = unquote(path).lstrip("/") or "index.html"
            try:
                target = (app.web_dir / rel).resolve()
                found = target.is_relative_to(app.web_dir) and target.is_file()
            except (ValueError, OSError):  # octet nul, nom trop long…
                found = False
            if not found:
                return self._send(404, "Introuvable".encode(), "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            # Revalidé à chaque chargement : après une mise à jour, pas d'anciens scripts gardés en cache.
            self._send(200, target.read_bytes(), ctype, headers=(("Cache-Control", "no-cache"),))

        def _live(self):
            q = app.bus.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=PING_S)
                        self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                app.bus.unsubscribe(q)

    httpd = ThreadingHTTPServer((app.cfg.server.host, app.cfg.server.port), Handler)
    httpd.daemon_threads = True
    return httpd


def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(log_dir / "monitoring.log", maxBytes=5 * 1024 * 1024,
                                       backupCount=4, encoding="utf-8")
    console = logging.StreamHandler()
    for h in (file_handler, console):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def install_signal_handlers(sig_module=signal) -> None:
    """Fermer la fenêtre Terminal (SIGHUP), un kill (SIGTERM) ou Ctrl-Pause sous Windows (SIGBREAK) passent par
    l'arrêt propre. Seuls les signaux qui existent sur ce système sont installés."""
    for name in ("SIGHUP", "SIGTERM", "SIGBREAK"):
        if hasattr(sig_module, name):
            sig_module.signal(getattr(sig_module, name), _interrupt)


def startup_message(cfg, port: int) -> str:
    irds = ", ".join(f"{e.name} ({e.host})" for e in cfg.irds) or "aucun IRD"
    fsh = f"{cfg.fsh.host}:{cfg.fsh.port}" if cfg.fsh.host else "aucun"
    return (f"Démarrage : FSH4 {fsh}, IRD : {irds}, "
            f"page http://{cfg.server.host}:{port}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Monitoring satellite FSH4 + IRD")
    parser.add_argument("--config", default=str(system.app_dir() / "config.toml"))
    parser.add_argument("--open", action="store_true", help="ouvrir la page dans le navigateur au démarrage")
    args = parser.parse_args(argv)
    cfg_path = Path(args.config).resolve()
    created = system.ensure_config(cfg_path)
    cfg = load_config(cfg_path)
    # Chemins relatifs de [paths] résolus par rapport au dossier de config.toml.
    base = cfg_path.parent
    cfg = replace(cfg, paths=replace(cfg.paths, db=str(base / cfg.paths.db), log_dir=str(base / cfg.paths.log_dir)))
    _setup_logging(Path(cfg.paths.log_dir))

    if created:
        log.info("Premier lancement : %s créé à partir du modèle", cfg_path)
    app = App(cfg, config_path=cfg_path)
    try:
        httpd = make_server(app)
    except OSError as e:
        # Le plus souvent, l'outil tourne déjà : on ouvre sa page plutôt qu'une trace incompréhensible.
        # Seule la connexion à la base est fermée : app.stop() refermerait les alertes de l'autre instance.
        app.store.close()
        port = cfg.server.port
        log.error("Impossible d'ouvrir la page : port %d déjà utilisé (l'outil est-il déjà lancé ?) — %s", port, e)
        if args.open:
            webbrowser.open(f"http://localhost:{port}")
        if system.frozen():
            input("Appuyer sur Entrée pour fermer cette fenêtre.")
        sys.exit(1)
    log.info(startup_message(cfg, httpd.server_address[1]))
    install_signal_handlers()
    awake = system.keep_awake()
    if awake:
        log.info(awake.capitalize())
    app.start()
    if args.open:
        webbrowser.open(f"http://localhost:{httpd.server_address[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.stop()
        log.info("Arrêt propre")


if __name__ == "__main__":
    main()
