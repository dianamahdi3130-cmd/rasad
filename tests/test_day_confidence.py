"""Daytime image candidates cannot confirm themselves through repeated slots."""
import unittest

from rasad.brain import (OWN_SEA_SOURCES, add_to_incident, day_requires_confirmation, day_source_only,
                         headline, new_incident, sea_finder_doubt, verdict_of)


class DayConfidenceTests(unittest.TestCase):
    def detection(self, src="mtg_day_spot", minute=0, conf=None, frp=None):
        family = {"VIIRS_NOAA21_NRT": "viirs", "MODIS_NRT": "modis",
                  "iodc_fire": "msg_iodc"}.get(src, "mtg")
        return {"src": src, "family": family,
                "kind": "polar" if family in {"viirs", "modis"} else "geo",
                "t": f"2026-11-08T06:{minute:02d}:00Z", "lat": 25.0, "lon": 57.0,
                "conf": conf, "frp": frp, "night": False}

    def incident(self, *detections):
        inc = new_incident(detections[0], "new")
        inc.update(place="sea", region_name="خليج عُمان", known_days=0)
        for det in detections:
            add_to_incident(inc, det)
        return inc

    def day_incident(self, slots=3):
        return self.incident(*(self.detection(minute=10 * i) for i in range(slots)))

    def test_day_only_needs_independent_confirmation_despite_repeated_slots(self):
        inc = self.day_incident(slots=6)

        verdict, reason = verdict_of(inc)

        self.assertEqual(verdict, "يحتاج تأكيداً")
        self.assertIn("نهارية تجريبية", reason)
        self.assertIn("مصدر مستقل", reason)
        self.assertTrue(day_source_only(inc))
        self.assertTrue(day_requires_confirmation(inc))

    def test_known_non_low_nasa_confidence_can_corroborate_day_candidate(self):
        for confidence in ("n", "nominal", "h", "high", "HIGH", 40, "40", 75, "40.0"):
            with self.subTest(confidence=confidence):
                inc = self.day_incident()
                add_to_incident(inc, self.detection("VIIRS_NOAA21_NRT", 30,
                                                  conf=confidence, frp=2))

                self.assertEqual(verdict_of(inc)[0], "مؤكد")
                self.assertFalse(day_source_only(inc))
                self.assertFalse(day_requires_confirmation(inc))

    def test_low_nasa_confidence_and_high_power_do_not_confirm_day_candidate(self):
        for confidence in ("l", "low", 0, 39, "39.9"):
            with self.subTest(confidence=confidence):
                inc = self.day_incident()
                add_to_incident(inc, self.detection("VIIRS_NOAA21_NRT", 30,
                                                  conf=confidence, frp=250))

                self.assertEqual(verdict_of(inc)[0], "يحتاج تأكيداً")
                self.assertTrue(day_requires_confirmation(inc))

    def test_unknown_nasa_confidence_does_not_confirm_day_candidate(self):
        for confidence in (None, "", "unknown", "unclassified"):
            with self.subTest(confidence=confidence):
                inc = self.day_incident()
                add_to_incident(inc, self.detection("VIIRS_NOAA21_NRT", 30,
                                                  conf=confidence, frp=250))

                self.assertEqual(verdict_of(inc)[0], "يحتاج تأكيداً")
                self.assertTrue(day_requires_confirmation(inc))

    def test_official_geo_product_unlocks_standard_slot_verdict(self):
        inc = self.day_incident(slots=2)
        add_to_incident(inc, self.detection("mtg_frp", 20))

        self.assertEqual(inc["families"], ["mtg"])
        self.assertEqual(verdict_of(inc)[0], "مؤكد")
        self.assertFalse(day_requires_confirmation(inc))

    def test_independent_official_geo_product_can_corroborate_day_candidate(self):
        inc = self.day_incident(slots=1)
        add_to_incident(inc, self.detection("iodc_fire", 10))

        self.assertEqual(verdict_of(inc)[0], "مؤكد")

    def test_day_and_night_finders_remain_one_unconfirmed_satellite(self):
        inc = self.day_incident()
        add_to_incident(inc, self.detection("mtg_spot", 30))

        self.assertEqual(inc["families"], ["mtg"])
        self.assertEqual(verdict_of(inc)[0], "يحتاج تأكيداً")
        self.assertEqual(inc["own_night_slots"], ["2026-11-08T06:30:00Z"])
        self.assertTrue(day_requires_confirmation(inc))

    def test_day_and_low_nasa_preserve_previously_probable_night_evidence(self):
        inc = self.incident(self.detection("mtg_spot"), self.detection("mtg_spot", 10))
        self.assertEqual(verdict_of(inc)[0], "مرجّح")
        original_night_slots = list(inc["own_night_slots"])

        for minute in (20, 30, 40):
            add_to_incident(inc, self.detection(minute=minute))
        add_to_incident(inc, self.detection("VIIRS_NOAA21_NRT", 50, conf="l", frp=250))

        verdict, reason = verdict_of(inc)
        self.assertEqual(verdict, "مرجّح")
        self.assertIn("صورتين", reason)
        self.assertEqual(inc["own_night_slots"], original_night_slots)
        self.assertFalse(day_requires_confirmation(inc))
        self.assertTrue(headline(inc, {"alerts": {"near_facility_km": 5}}).startswith("حريق"))

    def test_legacy_pure_night_slots_migrate_before_first_day_observation(self):
        inc = self.incident(self.detection("mtg_spot"), self.detection("mtg_spot", 10))
        del inc["own_night_slots"]  # Persisted state from before per-source slot tracking.
        original_night_slots = list(inc["geo_slots"])

        add_to_incident(inc, self.detection(minute=20))

        self.assertEqual(inc["own_night_slots"], original_night_slots)
        self.assertEqual(verdict_of(inc)[0], "مرجّح")
        self.assertFalse(day_requires_confirmation(inc))

    def test_legacy_night_slots_migrate_when_another_night_observation_arrives(self):
        inc = self.incident(self.detection("mtg_spot"))
        del inc["own_night_slots"]

        add_to_incident(inc, self.detection("mtg_spot", 10))
        add_to_incident(inc, self.detection(minute=20))

        self.assertEqual(len(inc["own_night_slots"]), 2)
        self.assertEqual(verdict_of(inc)[0], "مرجّح")

    def test_duplicate_night_and_day_slots_do_not_fabricate_second_night_observation(self):
        inc = self.incident(self.detection("mtg_spot"), self.detection("mtg_spot"))
        for minute in (10, 20, 30):
            add_to_incident(inc, self.detection(minute=minute))

        self.assertEqual(len(inc["own_night_slots"]), 1)
        self.assertEqual(verdict_of(inc)[0], "يحتاج تأكيداً")
        self.assertTrue(day_requires_confirmation(inc))

    def test_failed_close_review_prevents_retaining_night_probability(self):
        inc = self.incident(self.detection("mtg_spot"), self.detection("mtg_spot", 10))
        inc["unseen_close"] = True
        add_to_incident(inc, self.detection(minute=20))

        self.assertEqual(verdict_of(inc)[0], "يحتاج تأكيداً")
        self.assertTrue(day_requires_confirmation(inc))

    def test_day_only_never_creates_night_slots(self):
        inc = self.day_incident()

        self.assertFalse(inc.get("own_night_slots"))

    def test_day_only_headline_identifies_a_thermal_spot(self):
        inc = self.day_incident()

        title = headline(inc, {"alerts": {"near_facility_km": 5}})

        self.assertEqual(title, "بقعة حرارية نهارية في البحر قرب خليج عُمان")
        self.assertNotIn("حريق", title)

    def test_low_nasa_confidence_keeps_experimental_thermal_spot_headline(self):
        inc = self.day_incident()
        add_to_incident(inc, self.detection("VIIRS_NOAA21_NRT", 30, conf="l", frp=250))

        title = headline(inc, {"alerts": {"near_facility_km": 5}})

        self.assertTrue(title.startswith("بقعة حرارية نهارية في البحر"))
        self.assertNotIn("حريق", title)

    def test_doubt_applies_to_both_own_finders_and_their_combination(self):
        self.assertEqual(OWN_SEA_SOURCES, {"mtg_spot", "mtg_day_spot"})
        for srcs in (["mtg_spot"], ["mtg_day_spot"], list(OWN_SEA_SOURCES)):
            with self.subTest(srcs=srcs):
                self.assertTrue(sea_finder_doubt({"srcs": srcs, "unseen_close": True}))
        self.assertFalse(sea_finder_doubt({"srcs": ["mtg_day_spot", "mtg_frp"],
                                          "unseen_close": True}))

    def test_empty_sources_are_not_day_only(self):
        self.assertFalse(day_source_only({}))
        self.assertFalse(day_source_only({"srcs": []}))
        self.assertFalse(day_requires_confirmation({}))
        self.assertFalse(day_requires_confirmation({"srcs": []}))

    def test_existing_night_and_official_geo_verdicts_are_preserved(self):
        night = self.incident(*(self.detection("mtg_spot", 10 * i) for i in range(3)))
        official = self.incident(*(self.detection("mtg_frp", 10 * i) for i in range(3)))

        self.assertEqual(verdict_of(night)[0], "مرجّح")
        self.assertFalse(day_requires_confirmation(night))
        self.assertEqual(verdict_of(official)[0], "مؤكد")
        self.assertTrue(headline(night, {"alerts": {"near_facility_km": 5}}).startswith("حريق"))

    def test_existing_non_day_multi_family_verdict_is_preserved(self):
        inc = self.incident(self.detection("mtg_spot"),
                            self.detection("VIIRS_NOAA21_NRT", 10, conf="l", frp=2))

        self.assertEqual(verdict_of(inc)[0], "مؤكد")


if __name__ == "__main__":
    unittest.main()
