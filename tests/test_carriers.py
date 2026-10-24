import unittest

from monitoring.carriers import (Carrier, Tuning, attach_markers, band_hz, center_marker, compatible_pol,
                                 entry_matches, group_carriers)
from monitoring.config import CarrierEntry

V, H = "vertical", "horizontal"


def tuning(rf_khz, pol=V, sr=7_200_000, roll_off=0.25):
    return Tuning(rf_khz, pol, sr, roll_off)


BBC = {"A": Tuning(11_220_806, V, 24_760_000, 0.20)}
MARKERS = {1: 11_220.8e6, 2: 11_213e6, 4: 11_200e6, 5: 11_242e6}


class GroupTest(unittest.TestCase):
    def test_compatible_pol(self):
        self.assertTrue(compatible_pol(V, V))
        self.assertTrue(compatible_pol(V, None))
        self.assertTrue(compatible_pol(None, H))
        self.assertFalse(compatible_pol(V, H))

    def test_two_irds_same_carrier(self):
        carriers = group_carriers({"A": tuning(12_583_023), "B": tuning(12_583_060)}, ())
        self.assertEqual(len(carriers), 1)
        c = carriers[0]
        self.assertEqual(c.irds, ("A", "B"))
        self.assertEqual(c.key, "125830-V")
        self.assertEqual(c.name, "12 583,0 V")
        self.assertFalse(c.manual)

    def test_close_carriers_never_share_a_key(self):
        # Deux porteuses à faible débit symbole, à moins de 100 kHz l'une de l'autre : deux porteuses, deux clés.
        carriers = group_carriers({"A": tuning(12_583_010, sr=100_000), "B": tuning(12_583_090, sr=100_000)}, ())
        self.assertEqual(len(carriers), 2)
        self.assertEqual(len({c.key for c in carriers}), 2)
        # Clé gardée d'un calcul précédent par l'une, clé fraîche identique pour l'autre.
        again = group_carriers({"A": tuning(12_583_010, sr=100_000), "B": tuning(12_583_090, sr=100_000)}, (), carriers)
        self.assertEqual(len({c.key for c in again}), 2)
        self.assertEqual({c.key for c in again}, {c.key for c in carriers})

    def test_far_irds_two_carriers(self):
        carriers = group_carriers({"A": tuning(12_583_023), "B": tuning(12_590_000)}, ())
        self.assertEqual(len(carriers), 2)

    def test_same_freq_other_pol_two_carriers(self):
        carriers = group_carriers({"A": tuning(12_583_023, V), "B": tuning(12_583_023, H)}, ())
        self.assertEqual(sorted(c.key for c in carriers), ["125830-H", "125830-V"])

    def test_unknown_pol_joins(self):
        carriers = group_carriers({"A": tuning(12_583_023, V), "B": tuning(12_583_023, None)}, ())
        self.assertEqual(len(carriers), 1)
        self.assertEqual(carriers[0].polarization, V)

    def test_unknown_pol_name(self):
        c = group_carriers({"A": tuning(11_220_806, None)}, ())[0]
        self.assertEqual((c.key, c.name), ("112208-?", "11 220,8"))

    def test_manual_merges_and_names(self):
        entry = CarrierEntry("Client A", 12583.0, V, 7.2, True)
        carriers = group_carriers({"A": tuning(12_583_023)}, (entry,))
        self.assertEqual(len(carriers), 1)
        self.assertEqual((carriers[0].name, carriers[0].manual, carriers[0].irds), ("Client A", True, ("A",)))

    def test_manual_alone_kept(self):
        entry = CarrierEntry("Sans IRD", 11685.0, H, 27.5, True)
        carriers = group_carriers({"A": tuning(12_583_023)}, (entry,))
        self.assertEqual([c.name for c in carriers], ["Sans IRD", "12 583,0 V"])  # trié par fréquence
        self.assertEqual(carriers[0].irds, ())
        self.assertEqual(carriers[0].rf_khz, 11_685_000)

    def test_name_only_entry(self):
        entry = CarrierEntry("BBC", 12583.0, V, None, False)
        self.assertEqual(group_carriers({"A": tuning(12_583_023)}, (entry,))[0].name, "BBC")
        self.assertEqual(group_carriers({}, (entry,)), [])

    def test_entry_matches(self):
        c = group_carriers({"A": tuning(12_583_023)}, ())[0]
        self.assertTrue(entry_matches(c, CarrierEntry("x", 12583.0, V, 7.2, True)))
        self.assertTrue(entry_matches(c, CarrierEntry("x", 12583.02, None, None, False)))
        self.assertFalse(entry_matches(c, CarrierEntry("x", 12583.0, H, 7.2, True)))
        self.assertFalse(entry_matches(c, CarrierEntry("x", 12600.0, V, 7.2, True)))

    def test_center_ignores_downlink_when_one_measured(self):
        # L'IRD A décroche : sa fréquence devient celle configurée (erreur d'oscillateur du LNB comprise).
        carriers = group_carriers({"A": Tuning(12_584_060, V, 7_200_000, 0.25, measured=False),
                                   "B": Tuning(12_583_023, V, 7_200_000, 0.25)}, ())
        [c] = carriers
        self.assertEqual((c.key, c.rf_khz, c.irds), ("125830-V", 12_583_023, ("A", "B")))

    def test_previous_key_kept_across_100khz_boundary(self):
        before = group_carriers({"A": tuning(12_583_099)}, ())
        self.assertEqual(before[0].key, "125830-V")
        after = group_carriers({"A": tuning(12_583_101)}, (), previous=before)
        self.assertEqual(after[0].key, "125830-V")
        self.assertEqual(group_carriers({"A": tuning(12_583_101)}, ())[0].key, "125831-V")

    def test_previous_key_not_reused_twice(self):
        before = group_carriers({"A": tuning(12_583_000)}, ())
        after = group_carriers({"A": tuning(12_583_000), "B": tuning(12_583_000, H)}, (), previous=before)
        self.assertEqual(sorted(c.key for c in after), ["125830-H", "125830-V"])

    def test_previous_key_not_kept_when_polarization_becomes_known(self):
        before = group_carriers({"A": tuning(12_583_023, None)}, ())
        self.assertEqual(before[0].key, "125830-?")
        after = group_carriers({"A": tuning(12_583_023, H)}, (), previous=before)
        self.assertEqual(after[0].key, "125830-H")

    def test_band(self):
        c = group_carriers(BBC, ())[0]
        lo, hi = band_hz(c)
        self.assertAlmostEqual(hi - lo, 24.76e6 * 1.2, delta=1)
        no_roll = Carrier("k", "n", 11_000_000, V, 10_000_000, None, (), True)
        lo, hi = band_hz(no_roll)
        self.assertAlmostEqual(hi - lo, 12e6, delta=1)  # roll-off 0,20 par défaut


class MarkerTest(unittest.TestCase):
    def test_markers_attached_by_band(self):
        carriers = group_carriers(BBC, ())
        mm = attach_markers(carriers, MARKERS, V)
        self.assertEqual(mm.by_carrier, {carriers[0].key: (1, 2)})
        self.assertEqual(mm.noise, (4, 5))
        self.assertFalse(mm.if_warning)

    def test_markers_other_pol_not_attached(self):
        mm = attach_markers(group_carriers(BBC, ()), MARKERS, H)
        self.assertEqual(mm.by_carrier, {})

    def test_unknown_fsh_pol_attaches(self):
        mm = attach_markers(group_carriers(BBC, ()), MARKERS, None)
        self.assertEqual(list(mm.by_carrier.values()), [(1, 2)])

    def test_forced_noise_marker_leaves_its_carrier(self):
        # Marqueur imposé comme bruit alors qu'il tombe dans la bande d'une porteuse : il ne compte que pour le bruit.
        carriers = group_carriers(BBC, ())
        mm = attach_markers(carriers, MARKERS, V, noise_markers=(2, 4))
        self.assertEqual(mm.noise, (2, 4))
        self.assertEqual(mm.by_carrier[carriers[0].key], (1,))

    def test_noise_markers_override(self):
        mm = attach_markers(group_carriers(BBC, ()), MARKERS, V, noise_markers=(5,))
        self.assertEqual(mm.noise, (5,))

    def test_ambiguous_marker(self):
        carriers = group_carriers({"A": Tuning(11_220_806, V, 24_760_000, 0.2),
                                   "B": Tuning(11_220_806, H, 24_760_000, 0.2)}, ())
        mm = attach_markers(carriers, {1: 11_220.8e6}, None)
        self.assertEqual(mm.ambiguous, (1,))
        self.assertEqual(mm.by_carrier, {})

    def test_if_markers_warn_and_attach_nothing(self):
        mm = attach_markers(group_carriers(BBC, ()), {1: 1_470.8e6, 2: 1_480e6}, V)
        self.assertTrue(mm.if_warning)
        self.assertEqual((mm.by_carrier, mm.noise), ({}, ()))

    def test_center_marker(self):
        carriers = group_carriers(BBC, ())
        mm = attach_markers(carriers, MARKERS, V)
        self.assertEqual(center_marker(carriers[0], mm, MARKERS), 1)
        self.assertIsNone(center_marker(carriers[0], attach_markers(carriers, {}, V), {}))


if __name__ == "__main__":
    unittest.main()
