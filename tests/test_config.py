import tempfile
import unittest
from unittest import mock
from pathlib import Path

from monitoring.config import (CarrierEntry, Config, IrdEntry, load_config, save_carriers, save_detect, save_fsh,
                               save_irds)


def write(text):
    d = tempfile.mkdtemp()
    p = Path(d) / "c.toml"
    p.write_text(text, encoding="utf-8")
    return p


class ConfigTest(unittest.TestCase):
    def test_defaults_when_file_has_only_comments(self):
        cfg = load_config(write("# vide\n"))
        self.assertEqual(cfg, Config())
        self.assertEqual(cfg.fsh.port, 5555)
        self.assertEqual(cfg.detect.ber_threshold, 1e-6)
        self.assertEqual(cfg.server.host, "127.0.0.1")

    def test_bom_from_windows_notepad(self):
        # Bloc-notes de Windows : « UTF-8 avec BOM ». Le fichier doit se lire et s'enregistrer quand même.
        path = write("")
        path.write_bytes("\ufeff[fsh]\nhost = \"10.0.0.9\"\n".encode("utf-8"))
        self.assertEqual(load_config(path).fsh.host, "10.0.0.9")
        save_fsh(path, "10.0.0.8", 5555, None)
        self.assertEqual(load_config(path).fsh.host, "10.0.0.8")
        self.assertEqual(path.read_text(encoding="utf-8").count("[fsh]"), 1)

    def test_invalid_values_refused_in_french(self):
        for text, expected in (("[fsh]\nrate_hz = 0\n", "rate_hz"), ("[fsh]\nrate_hz = \"vite\"\n", "rate_hz"),
                               ("[server]\nport = 70000\n", "port"), ("[fsh]\nhost = 12\n", "host"),
                               ("[[carriers]]\nname = \"X\"\nmanual = true\n", "freq_mhz"),
                               ("[[carriers]]\nname = \"X\"\nfreq_mhz = 99999999.0\nmanual = true\n", "freq_mhz")):
            with self.assertRaises(ValueError, msg=text) as ctx:
                load_config(write(text))
            self.assertIn(expected, str(ctx.exception))

    def test_spaced_section_header(self):
        # « [ fsh ] » écrit à la main : enregistrer ne doit pas ajouter un second [fsh] (fichier illisible ensuite).
        path = write("[ fsh ]\nhost = \"10.0.0.9\"\n")
        save_fsh(path, "10.0.0.8", 5555, None)
        self.assertEqual(load_config(path).fsh.host, "10.0.0.8")

    def test_comments_between_blocks_kept(self):
        path = write("[[irds]]\nserial = \"A\"\nname = \"IRD 1\"\nhost = \"10.0.0.1\"\n\n"
                     "# Seuils réglés pour ce site\n[detect]\nmargin_drop_db = 2.0\n")
        save_irds(path, (IrdEntry("A", "IRD 1", "10.0.0.2"),))
        self.assertIn("# Seuils réglés pour ce site\n[detect]", path.read_text(encoding="utf-8"))

    def test_write_retries_when_windows_holds_the_file(self):
        # Antivirus ou indexeur qui garde config.toml ouvert un instant : le remplacement est retenté.
        path = write("[fsh]\nhost = \"10.0.0.9\"\n")
        real = Path.replace
        calls = []

        def flaky(self, target):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("fichier utilisé par un autre processus")
            return real(self, target)

        with mock.patch.object(Path, "replace", flaky):
            save_fsh(path, "10.0.0.8", 5555, None)
        self.assertEqual(load_config(path).fsh.host, "10.0.0.8")

    def test_override_one_key(self):
        cfg = load_config(write("[fsh]\nrate_hz = 2.0\n"))
        self.assertEqual(cfg.fsh.rate_hz, 2.0)
        self.assertEqual(cfg.fsh.host, "192.168.20.222")

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            load_config(write('[fsh]\nhots = "x"\n'))

    def test_unknown_section_raises(self):
        with self.assertRaises(ValueError):
            load_config(write("[fhs]\n"))

    def test_example_config_loads(self):
        # Le modèle livré (copié en config.toml au premier lancement) : config.toml n'est plus suivi par git.
        root = Path(__file__).resolve().parent.parent
        cfg = load_config(root / "config.example.toml")
        self.assertEqual(cfg.paths.retention_days, 30)
        self.assertEqual(cfg.fsh.min_idle_s, 0.2)
        self.assertTrue(all(isinstance(e, IrdEntry) for e in cfg.irds))

    def test_example_config_has_no_device(self):
        root = Path(__file__).resolve().parent.parent
        cfg = load_config(root / "config.example.toml")
        self.assertEqual((cfg.fsh.host, cfg.irds, cfg.carriers, cfg.detect.noise_markers, cfg.detect.margin_min_db),
                         ("", (), (), (), 0.0))

    def test_fsh_min_idle_default(self):
        self.assertEqual(Config().fsh.min_idle_s, 0.2)

    def test_noise_markers(self):
        cfg = load_config(write("[detect]\nnoise_markers = [4, 5]\n"))
        self.assertEqual(cfg.detect.noise_markers, (4, 5))
        self.assertEqual(Config().detect.noise_markers, ())
        for bad in ("[7]", "[0]", '["4"]', "4"):
            with self.assertRaises(ValueError, msg=bad):
                load_config(write(f"[detect]\nnoise_markers = {bad}\n"))

    def repo_copy(self):
        root = Path(__file__).resolve().parent.parent
        return write((root / "config.example.toml").read_text(encoding="utf-8"))  # config.toml n'est pas suivi

    def test_irds_and_carriers_roundtrip(self):
        p = self.repo_copy()
        irds = (IrdEntry("1000-00001", "IRD 1", "192.168.10.141"), IrdEntry("", 'IRD "test"', "10.0.0.7"))
        carriers = (CarrierEntry("BBC", 11220.8, "vertical", 24.76, True), CarrierEntry("Auto", 12583.0, None, None, False))
        save_irds(p, irds)
        save_carriers(p, carriers)
        save_irds(p, irds)  # réécriture : pas de doublon
        cfg = load_config(p)
        self.assertEqual(cfg.irds, irds)
        self.assertEqual(cfg.carriers, carriers)
        self.assertIn("# analyseur R&S FSH4", p.read_text(encoding="utf-8"))
        self.assertEqual(cfg.paths.retention_days, 30)

    def test_control_characters_keep_config_readable(self):
        # Un nom venu de l'IRD (systemName) ou de la page ne doit jamais rendre config.toml illisible.
        p = self.repo_copy()
        irds = (IrdEntry("1000-00001", "IRD\x7f 1\x01\ttab", "192.168.10.141"),)
        save_irds(p, irds)
        save_carriers(p, (CarrierEntry("Porteuse\x7f", 12583.0, None, None, False),))
        cfg = load_config(p)
        self.assertEqual(cfg.irds, irds)
        self.assertEqual(cfg.carriers[0].name, "Porteuse\x7f")

    def test_ird_polarization_roundtrip(self):
        p = self.repo_copy()
        irds = (IrdEntry("S1", "IRD 1", "10.0.0.1", "horizontal"), IrdEntry("S2", "IRD 2", "10.0.0.2"))
        save_irds(p, irds)
        self.assertEqual(load_config(p).irds, irds)
        self.assertIsNone(load_config(p).irds[1].polarization)
        with self.assertRaises(ValueError):
            load_config(write('[[irds]]\nserial = "S1"\nname = "x"\nhost = "h"\npolarization = "oblique"\n'))

    def test_save_irds_empty(self):
        p = self.repo_copy()
        save_irds(p, ())
        self.assertEqual(load_config(p).irds, ())

    def test_legacy_ird_url_becomes_one_ird(self):
        cfg = load_config(write('[ird]\nurl = "http://192.168.10.141/xml-rpc"\nrate_hz = 2.0\n'))
        self.assertEqual(cfg.irds, (IrdEntry("", "IRD", "192.168.10.141"),))
        self.assertEqual(cfg.ird.rate_hz, 2.0)

    def test_save_irds_drops_legacy_url(self):
        p = write('[ird]   # commentaire\nurl = "http://192.168.10.141/xml-rpc" # ancien\nrate_hz = 1.0\n')
        save_irds(p, (IrdEntry("1000-00001", "IRD 1", "192.168.10.141"),))
        self.assertNotIn("url", p.read_text(encoding="utf-8"))
        self.assertEqual(len(load_config(p).irds), 1)

    def test_save_detect_thresholds(self):
        p = self.repo_copy()
        before = p.read_text(encoding="utf-8")
        save_detect(p, 2.5, 3.0)
        cfg = load_config(p)
        self.assertEqual((cfg.detect.margin_drop_db, cfg.detect.margin_min_db), (2.5, 3.0))
        after = p.read_text(encoding="utf-8")
        self.assertEqual(before.count("#"), after.count("#"))  # commentaires conservés

    def test_margin_min_off_by_default(self):
        self.assertEqual(load_config(write("")).detect.margin_min_db, 0.0)

    def test_fsh_polarization(self):
        p = self.repo_copy()
        save_fsh(p, "192.168.10.224", 5555, "vertical")
        cfg = load_config(p)
        self.assertEqual((cfg.fsh.host, cfg.fsh.port, cfg.fsh.polarization), ("192.168.10.224", 5555, "vertical"))
        save_fsh(p, "192.168.10.224", 5555, None)
        self.assertIsNone(load_config(p).fsh.polarization)
        with self.assertRaises(ValueError):
            load_config(write('[fsh]\npolarization = "oblique"\n'))


if __name__ == "__main__":
    unittest.main()
