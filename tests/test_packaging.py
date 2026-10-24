import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

spec = importlib.util.spec_from_file_location("monitoring_sat", Path(__file__).parent.parent / "packaging" / "monitoring_sat.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTest(unittest.TestCase):
    def test_startup_error_stays_readable_in_exe(self):
        # Exécutable Windows : une erreur au démarrage (dossier en lecture seule, config.toml invalide…) ne doit pas
        # fermer la fenêtre avant qu'on ait pu lire le message.
        def broken(_args):
            raise PermissionError("config.toml : accès refusé")

        paused, err = [], io.StringIO()
        with redirect_stderr(err):
            code = launcher.run(["--no-open"], main=broken, frozen=True, pause=lambda prompt: paused.append(prompt))
        self.assertEqual(code, 1)
        self.assertEqual(len(paused), 1)
        self.assertIn("config.toml : accès refusé", err.getvalue())

    def test_no_pause_outside_exe(self):
        def broken(_args):
            raise RuntimeError("boum")

        with self.assertRaises(RuntimeError):
            launcher.run([], main=broken, frozen=False, pause=lambda prompt: None)
