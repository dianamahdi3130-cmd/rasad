"""Retain satellite evidence so nearby events are not joined without spatial support."""
import copy
import unittest

from rasad.brain import (add_to_incident, association_requires_confirmation, find_incident,
                         headline, new_incident, verdict_of)


class IncidentProvenanceTests(unittest.TestCase):
    @staticmethod
    def detection(src="VIIRS_NOAA20_NRT", family="viirs", t="2026-11-08T10:21:00Z",
                  lat=26.0998, lon=55.9036, frp=26.4, **extra):
        return {"src": src, "family": family, "kind": "polar", "t": t,
                "lat": lat, "lon": lon, "frp": frp, "conf": "n", "night": False, **extra}

    def test_each_source_keeps_its_acquisition_position_and_pixel_footprint(self):
        first = self.detection(scan=0.5, track=0.7)
        newer = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                               26.0857, 55.9091, 47.4, scan=1.8, track=1.4)
        inc = new_incident(first, "new")
        add_to_incident(inc, first)
        add_to_incident(inc, newer)

        self.assertEqual((inc["lat"], inc["lon"]), (first["lat"], first["lon"]))
        for original, kept in zip((first, newer), inc["polar"]):
            self.assertEqual({key: kept[key] for key in ("src", "family", "lat", "lon", "scan", "track")},
                             {key: original[key] for key in ("src", "family", "lat", "lon", "scan", "track")})
        self.assertEqual([o["t"] for o in inc["observations"]], [first["t"], newer["t"]])

    def test_legacy_positions_remain_unknown_rather_than_using_the_display_anchor(self):
        first = self.detection()
        inc = new_incident(first, "new")
        add_to_incident(inc, first)
        del inc["observations"]
        old = {key: inc["polar"][0][key] for key in ("t", "frp", "conf", "night")}
        inc["polar"] = [copy.deepcopy(old)]
        add_to_incident(inc, self.detection(t="2026-11-08T17:02:00Z", lat=26.0857))

        self.assertEqual(inc["polar"][0], old)
        self.assertNotIn("lat", inc["polar"][0])
        self.assertEqual(len(inc["observations"]), 1)
        self.assertEqual(inc["observations"][0]["lat"], 26.0857)

    def test_identical_observations_do_not_duplicate_provenance(self):
        det = self.detection()
        inc = new_incident(det, "new")
        add_to_incident(inc, det)
        add_to_incident(inc, copy.deepcopy(det))

        self.assertEqual(len(inc["polar"]), 1)
        self.assertEqual(len(inc["observations"]), 1)


class PrecisionMergeTests(unittest.TestCase):
    detection = staticmethod(IncidentProvenanceTests.detection)
    def incident(self, *detections):
        inc = new_incident(detections[0], "new")
        inc.update(place="sea", facility=None, region_name="مضيق هرمز")
        for det in detections:
            add_to_incident(inc, det)
        return inc

    def actual_prior(self):
        points = [("08:41", 26.1034, 55.8839), ("08:41", 26.1094, 55.8821),
                  ("10:21", 26.0995, 55.8982), ("10:21", 26.0998, 55.9036),
                  ("10:21", 26.1015, 55.8995), ("10:21", 26.1019, 55.9049)]
        return self.incident(*(self.detection(t=f"2026-11-08T{clock}:00Z", lat=lat, lon=lon,
                                              **({"scan": 0.54, "track": 0.68} if clock == "10:21" else {}))
                               for clock, lat, lon in points))

    def test_actual_later_modis_pass_is_kept_separate_without_modifying_old_peak(self):
        prior = self.actual_prior()
        original = copy.deepcopy(prior)
        later = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                               26.08411, 55.91482, 47.42, conf="100", scan=2.15, track=1.42,
                               satellite="T")

        self.assertIsNone(find_incident([prior], later, 6, 12))
        candidate = self.incident(later)

        self.assertEqual(prior, original)
        self.assertEqual(prior["max_frp"], 26.4)
        self.assertTrue(association_requires_confirmation(candidate))
        self.assertEqual(candidate["association"]["previous_id"], prior["id"])
        self.assertEqual(verdict_of(candidate)[0], "يحتاج تأكيداً")
        self.assertIn("بقعة حرارية", headline(candidate, {"alerts": {"near_facility_km": 10}}))
        self.assertNotIn("حريق", headline(candidate, {"alerts": {"near_facility_km": 10}}))

    def test_actual_pass_does_not_depend_on_which_modis_pixel_is_processed_first(self):
        points = [(26.0841, 55.9148), (26.0857, 55.9091),
                  (26.0861, 55.9358), (26.0876, 55.9301)]
        for seed in range(4):
            with self.subTest(seed=seed):
                prior, candidate = self.actual_prior(), None
                for lat, lon in points[seed:] + points[:seed]:
                    det = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                                         lat, lon, 47.4, conf="80")
                    match = find_incident([prior] + ([candidate] if candidate else []), det, 6, 12)
                    if candidate is None:
                        self.assertIsNone(match)
                        candidate = self.incident(det)
                    else:
                        self.assertIs(match, candidate)
                        add_to_incident(candidate, det)
                self.assertEqual(prior["max_frp"], 26.4)
                self.assertTrue(association_requires_confirmation(candidate))

    def test_close_return_after_a_long_gap_remains_an_update(self):
        det = self.detection(lat=26.1, lon=55.9)
        prior = self.incident(det)
        newer = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                               26.101, 55.901, 47.4)

        self.assertIs(find_incident([prior], newer, 6, 12), prior)
        self.assertNotIn("_association", newer)

    def test_known_coarse_footprint_overlap_can_still_match_without_a_long_gap(self):
        prior = self.actual_prior()
        newer = self.detection("MODIS_NRT", "modis", "2026-11-08T11:02:00Z",
                               26.08411, 55.91482, 47.42, conf="100", scan=2.15, track=1.42)

        self.assertIs(find_incident([prior], newer, 6, 12), prior)
        self.assertNotIn("_association", newer)

    def test_distinct_precise_pixels_do_not_merge_within_global_six_km_radius(self):
        prior = self.incident(self.detection(lat=26.1, lon=55.9, scan=0.375, track=0.375))
        newer = self.detection(t="2026-11-08T10:22:00Z", lat=26.1, lon=55.93,
                               scan=0.375, track=0.375)

        self.assertIsNone(find_incident([prior], newer, 6, 12))
        self.assertIn("_association", newer)

    def test_coarse_geostationary_symbol_keeps_the_existing_wider_match(self):
        prior = self.incident(self.detection(lat=26.1, lon=55.9, scan=0.375, track=0.375))
        newer = self.detection("mtg_frp", "mtg", "2026-11-08T10:30:00Z", 26.1, 55.94)
        newer["kind"] = "geo"

        self.assertIs(find_incident([prior], newer, 6, 12), prior)

    def candidate(self):
        prior = self.actual_prior()
        det = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                             26.0857, 55.9091, 47.4, conf="80")
        find_incident([prior], det, 6, 12)
        return self.incident(det)

    def test_same_pass_or_weak_or_distant_detections_cannot_resolve_association(self):
        for changes in ({}, {"src": "VIIRS_NOAA21_NRT", "family": "viirs", "conf": "l"},
                        {"src": "VIIRS_NOAA21_NRT", "family": "viirs", "lat": 26.14}):
            with self.subTest(changes=changes):
                candidate = self.candidate()
                newer = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                                       26.0857, 55.9091, 47.4, conf="80")
                newer.update(changes)
                add_to_incident(candidate, newer)
                self.assertTrue(association_requires_confirmation(candidate))

    def test_clear_independent_local_observation_resolves_the_candidate(self):
        candidate = self.candidate()
        newer = self.detection("VIIRS_NOAA21_NRT", "viirs", "2026-11-08T17:20:00Z",
                               26.0858, 55.9092, 45.0, conf="n")

        add_to_incident(candidate, newer)

        self.assertFalse(association_requires_confirmation(candidate))
        self.assertEqual(candidate["association_resolved_at"], newer["t"])
        self.assertEqual(verdict_of(candidate)[0], "مؤكد")

    def test_legacy_polar_readings_do_not_gain_made_up_positions(self):
        prior = self.actual_prior()
        prior.pop("observations")
        prior["polar"] = [{key: row[key] for key in ("t", "frp", "conf", "night")}
                          for row in prior["polar"]]
        old = copy.deepcopy(prior)
        newer = self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z",
                               26.0857, 55.9091, 47.4)

        # Future distant acquisitions are held apart, but historical state is untouched.
        self.assertIsNone(find_incident([prior], newer, 6, 12))
        self.assertTrue(newer["_association"]["legacy_positions_unknown"])
        self.assertEqual(prior, old)

    def test_legacy_close_return_or_short_gap_keeps_the_existing_match(self):
        for lat, lon, clock in ((26.104, 55.884, "17:02"), (26.0857, 55.9091, "11:02")):
            with self.subTest(clock=clock):
                prior = self.actual_prior()
                prior.pop("observations")
                prior["polar"] = [{key: row[key] for key in ("t", "frp", "conf", "night")}
                                  for row in prior["polar"]]
                newer = self.detection("MODIS_NRT", "modis", f"2026-11-08T{clock}:00Z",
                                       lat, lon, 47.4)

                self.assertIs(find_incident([prior], newer, 6, 12), prior)
                self.assertNotIn("_association", newer)


if __name__ == "__main__":
    unittest.main()
