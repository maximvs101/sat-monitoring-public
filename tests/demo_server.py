"""Serveur de démonstration, sans réseau ni appareil : deux IRD sur une porteuse, un troisième sur une autre,
un FSH4 simulé. L'IRD 2 décroche toutes les 40 s pour montrer « IRD seul en défaut » ; l'IRD 3 perd l'image
toutes les 60 s pendant 6 s (et le note dans son journal).

    python3 -m tests.demo_server [port]      (8091 par défaut ; base temporaire, effacée à l'arrêt)
"""

import math
import random
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from monitoring.app import App
from monitoring.config import IrdEntry, PathsConfig, ServerConfig, load_config, save_irds
from monitoring.fsh import FshReading, FshSettings, MarkerSample
from monitoring.ird import AudioVoice, IrdDetails, IrdError, IrdHealth, IrdLogMessage, IrdReading, IrdSample
from monitoring.server import make_server

DEVICES = {"10.0.0.1": ("DEMO-1", "IRD 1"), "10.0.0.2": ("DEMO-2", "IRD 2"), "10.0.0.3": ("DEMO-3", "IRD 3")}
TUNING = {"DEMO-1": (12_583_023, 7_200_000), "DEMO-2": (12_583_050, 7_200_000), "DEMO-3": (11_220_806, 24_760_000)}
PROGRAMS = {"DEMO-1": (1, "Programme A"), "DEMO-2": (1, "Programme A"), "DEMO-3": (3, "Programme B")}
EXTRAS_EVERY_S = 10


def video_lost(serial: str) -> bool:
    return serial == "DEMO-3" and int(time.time()) % 60 < 6


def ird_sample(serial: str) -> IrdSample:
    rf, sr = TUNING[serial]
    margin = (6.3 if serial != "DEMO-3" else 10.5) + random.gauss(0, 0.15)
    locked = True
    if serial == "DEMO-2" and int(time.time()) % 40 < 8:  # décrochage périodique de l'IRD 2
        locked, margin = False, 0.0
    # Polarisation inconnue : comme sur la plupart des sites, les IRD n'alimentent pas le LNB.
    video = locked and not video_lost(serial)
    number, name = PROGRAMS[serial]
    return IrdSample(locked=locked, ts_locked=locked, cn_db=margin + 5, margin_db=margin, ber=0.0 if locked else 0.1,
                     ber_raw="0", power_dbm=-40.0, if_khz=rf - 10_600_000, symbol_rate=sr, modcod="dvbs2 8psk 3/4",
                     bitrate_kbps=20000 if locked else 0, rf_khz=rf, polarization=None, roll_off=0.25, rf_measured=locked,
                     video_active=video, video_bitrate=13_600_000 if video else 0, video_format="hd_1920x1080i_50",
                     audio_configured=2, audio_active=2 if video else 0, audio_bitrate=192_000 if video else None,
                     scrambled=False, descrambled=False, program_number=number, service_name=name,
                     details=IrdDetails("h264", "h264_high", "h264_4", 1920, 1080, 25.0, "16_9", 512,
                                        tuple(AudioVoice(k, 4112 + 16 * k, "mpa", 2, 48000, 192_000, video)
                                              for k in range(2)),
                                        ("hd_1920x1080i_50", "hd_1920x1080i_50"), True, 0, False, "blue", 1000))


def ird_logs(serial: str) -> list:
    if serial != "DEMO-3":
        return []
    t = int(time.time())
    # Un message par perte d'image ; nId croissant d'une minute à l'autre, comme sur l'IRD.
    return [IrdLogMessage(t // 60, "Video decoder: no input", "warning", ended=not video_lost(serial))]


def factory(entry, cfg):
    def identify():
        return DEVICES[entry.host]

    last_extras = [0.0]

    def read():
        serial, _ = identify()
        if entry.serial and serial != entry.serial:
            raise IrdError("autre appareil")
        if time.time() - last_extras[0] < EXTRAS_EVERY_S:
            return IrdReading(ird_sample(serial))
        last_extras[0] = time.time()
        health = IrdHealth(47 + random.gauss(0, 0.3), 41 + random.gauss(0, 0.3), False, False, False)
        return IrdReading(ird_sample(serial), health, ird_logs(serial))

    return read, identify


def fsh_read() -> FshReading:
    t = time.time()
    markers = {1: MarkerSample(12_583.0e6, -50 + 0.8 * math.sin(t / 7) + random.gauss(0, 0.5)),
               4: MarkerSample(12_570e6, -67 + random.gauss(0, 0.8)),
               5: MarkerSample(12_596e6, -68 + random.gauss(0, 0.8))}
    return FshReading(markers, FshSettings(50e6, 300e3, tuple((n, m.freq_hz) for n, m in markers.items())))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    tmp = Path(tempfile.mkdtemp(prefix="monitoring-demo-"))
    cfg_path = tmp / "config.toml"
    cfg_path.write_text('[fsh]\nhost = "10.0.0.9"\npolarization = "vertical"\n', encoding="utf-8")
    save_irds(cfg_path, tuple(IrdEntry(s, n, h) for h, (s, n) in DEVICES.items()))
    cfg = load_config(cfg_path)
    cfg = replace(cfg, server=ServerConfig(port=port), paths=PathsConfig(db=str(tmp / "demo.db"), log_dir=str(tmp)))
    app = App(cfg, fsh_read=fsh_read, fsh_close=lambda: None, ird_factory=factory, config_path=cfg_path,
              scanner=lambda: {"networks": ["10.0.0.0/24"], "fsh": [], "ird": []})
    httpd = make_server(app)
    app.start()
    print(f"Démonstration sur http://127.0.0.1:{port} (base {tmp})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.stop()


if __name__ == "__main__":
    main()
