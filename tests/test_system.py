import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from monitoring import system


class SystemTest(unittest.TestCase):
    def test_paths_from_source(self):
        root = Path(system.__file__).resolve().parent.parent
        self.assertEqual(system.app_dir(), root)
        self.assertEqual(system.web_dir(), root / "web")
        self.assertEqual(system.example_config(), root / "config.example.toml")
        self.assertTrue(system.example_config().is_file())

    def test_paths_when_frozen(self):
        # Exécutable PyInstaller : config.toml, base et journaux à côté de l'exe ; page et modèle dans le paquet.
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "/opt/outil/MonitoringSat.exe"), \
                mock.patch.object(sys, "_MEIPASS", "/tmp/_MEI123", create=True):
            # resolve() : sous Windows, « /opt/outil » devient « C:\\opt\\outil ».
            self.assertEqual(system.app_dir(), Path("/opt/outil/MonitoringSat.exe").resolve().parent)
            self.assertEqual(system.web_dir(), Path("/tmp/_MEI123/web"))
            self.assertEqual(system.example_config(), Path("/tmp/_MEI123/config.example.toml"))

    def test_ensure_config_copies_example_once(self):
        from monitoring.config import load_config
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.toml"
            self.assertTrue(system.ensure_config(target))
            cfg = load_config(target)
            self.assertEqual((cfg.irds, cfg.server.host), ((), "127.0.0.1"))
            target.write_text(target.read_text(encoding="utf-8") + "# réglé\n", encoding="utf-8")
            self.assertFalse(system.ensure_config(target))  # jamais écrasé
            self.assertTrue(target.read_text(encoding="utf-8").endswith("# réglé\n"))

    def test_keep_awake_windows(self):
        calls = []
        kernel = types.SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(flags) or 1)
        self.assertEqual(system.keep_awake("win32", kernel32=kernel), "veille désactivée tant que l'outil tourne")
        self.assertEqual(calls, [0x80000000 | 0x00000001])  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

    def test_keep_awake_mac(self):
        started = []
        with mock.patch("subprocess.Popen", lambda args, **kw: started.append(args)), \
                mock.patch("shutil.which", lambda name: "/usr/bin/caffeinate"):
            system.keep_awake("darwin")
        self.assertEqual(started[0][:3], ["/usr/bin/caffeinate", "-i", "-w"])

    def test_keep_awake_elsewhere_is_harmless(self):
        self.assertIsNone(system.keep_awake("linux"))


if __name__ == "__main__":
    unittest.main()
