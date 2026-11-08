"""Measured-strength selection, update gating, and factual Telegram wording."""
import copy
import datetime as dt
import unittest

from rasad.brain import alert_message
from rasad.updates import intensity_update_due, latest_reading, peak_reading
from rasad.util import UTC


class ReadingTests(unittest.TestCase):
    def test_peak_and_latest_measurements_are_distinct_across_sources(self):
        inc = {"frp_series": [["2026-11-08T11:00:00Z", 51, "mtg"],
                              ["2026-11-08T11:50:00Z", 164, "mtg"]],
               "polar": [{"t": "2026-11-08T11:30:00Z", "frp": 181}]}
        self.assertEqual(peak_reading(inc), ("2026-11-08T11:30:00Z", 181.0))
        self.assertEqual(latest_reading(inc), ("2026-11-08T11:50:00Z", 164.0))

    def test_same_instant_uses_largest_value_and_peak_ties_use_latest_time(self):
        inc = {"frp_series": [["2026-11-08T11:30:00Z", 164],
                              ["2026-11-08T11:50:00Z", 164]],
               "polar": [{"t": "2026-11-08T14:50:00+03:00", "frp": 120}]}
        self.assertEqual(peak_reading(inc), ("2026-11-08T11:50:00Z", 164.0))
        self.assertEqual(latest_reading(inc), ("2026-11-08T11:50:00Z", 164.0))

    def test_invalid_nonfinite_negative_and_missing_measurements_are_ignored(self):
        invalid = [None, float("nan"), float("inf"), "-inf", -1, "bad", True]
        inc = {"frp_series": [["2026-11-08T11:50:00Z", value] for value in invalid],
               "polar": [{"t": "invalid time", "frp": 500}, {"frp": 900},
                         {"t": "2026-11-08T11:00:00Z", "frp": "51"}]}
        self.assertEqual(peak_reading(inc), ("2026-11-08T11:00:00Z", 51.0))
        self.assertEqual(latest_reading(inc), ("2026-11-08T11:00:00Z", 51.0))
        self.assertIsNone(peak_reading({}))
        self.assertIsNone(latest_reading({"polar": [{"frp": float("nan")}]}))


class IntensityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)
        self.cfg = {"alerts": {"max_age_hours": 8}}
        self.inc = {"status": "alerted", "alerted_max_frp": 51,
                    "alerted_at": "2026-11-08T11:00:00Z", "last": "2026-11-08T11:59:00Z",
                    "frp_series": [["2026-11-08T11:50:00Z", 164, "mtg"]]}

    def test_large_increase_qualifies_without_mutating_delivery_state(self):
        before = copy.deepcopy(self.inc)
        self.assertTrue(intensity_update_due(self.inc, self.cfg, self.now))
        self.assertEqual(self.inc, before)

    def test_small_fluctuation_after_164_mw_does_not_repeat_update(self):
        self.inc["alerted_max_frp"] = 164
        self.inc["frp_series"][0][1] = 181
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_both_relative_and_absolute_increase_are_required(self):
        for baseline, current in ((51, 76), (1, 1.75), (164, 150), (51, 51)):
            with self.subTest(baseline=baseline, current=current):
                self.inc["alerted_max_frp"] = baseline
                self.inc["frp_series"][0][1] = current
                self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_cooldown_uses_latest_successful_update_and_expires_without_new_reading(self):
        self.inc["alert_updated_at"] = "2026-11-08T11:45:00Z"
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))
        later = self.now + dt.timedelta(minutes=15)
        self.assertTrue(intensity_update_due(self.inc, self.cfg, later))

    def test_stale_or_future_peak_is_rejected_even_with_fresh_incident_last(self):
        for when in ("2026-11-07T11:59:00Z", "2026-11-08T12:01:00Z"):
            with self.subTest(when=when):
                self.inc["frp_series"][0][0] = when
                self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_legacy_missing_baseline_requires_migration(self):
        del self.inc["alerted_max_frp"]
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_previously_unmeasured_fire_needs_one_mw_before_update(self):
        self.inc["alerted_max_frp"] = None
        self.inc["frp_series"][0][1] = 0.9
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))
        self.inc["frp_series"][0][1] = 1
        self.assertTrue(intensity_update_due(self.inc, self.cfg, self.now))

    def test_small_oman_fire_increase_is_not_blocked_by_first_alert_age_or_25_mw(self):
        self.inc.update(alerted_max_frp=2.41, alerted_at="2026-11-08T00:00:13Z",
                        alert_updated_at="2026-11-08T00:00:13Z", last="2026-11-07T23:05:00Z",
                        frp_series=[["2026-11-07T22:09:00Z", 2.4, "viirs"],
                                    ["2026-11-07T23:05:00Z", 13.4, "viirs"]],
                        polar=[{"t": "2026-11-07T23:05:00Z", "frp": 13.39}])
        before = copy.deepcopy(self.inc)
        now = dt.datetime(2026, 11, 8, 9, 40, tzinfo=UTC)

        self.assertTrue(intensity_update_due(self.inc, self.cfg, now))
        self.assertEqual(self.inc, before, "eligibility must not consume a failed delivery's baseline")

    def test_sub_megawatt_noise_does_not_trigger_despite_large_relative_change(self):
        self.inc["alerted_max_frp"] = 0.2
        self.inc["frp_series"][0][1] = 0.8
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_update_window_is_separate_configurable_and_bounded_at_24_hours(self):
        self.inc["frp_series"][0][0] = "2026-11-07T12:00:00Z"
        self.assertTrue(intensity_update_due(self.inc, self.cfg, self.now))
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now + dt.timedelta(seconds=1)))
        self.cfg["alerts"]["intensity_max_age_hours"] = 12
        self.inc["frp_series"][0][0] = "2026-11-08T01:00:00Z"
        self.assertTrue(intensity_update_due(self.inc, self.cfg, self.now))
        self.inc["frp_series"][0][0] = "2026-11-07T23:00:00Z"
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_nonfinite_baseline_or_peak_is_not_a_trigger(self):
        for invalid in (float("nan"), float("inf"), "bad"):
            with self.subTest(invalid=invalid):
                self.inc["alerted_max_frp"] = invalid
                self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))
                self.inc["alerted_max_frp"] = 51
                self.inc["frp_series"][0][1] = invalid
                self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_only_previously_alerted_incidents_can_emit_intensity_updates(self):
        for status in ("new", "pending", "held", "late"):
            with self.subTest(status=status):
                self.inc["status"] = status
                self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))

    def test_explicit_threshold_and_interval_settings_are_respected(self):
        self.cfg["alerts"].update(intensity_ratio=4, intensity_add_mw=200, intensity_interval_min=90)
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))
        self.inc["frp_series"][0][1] = 300
        self.assertFalse(intensity_update_due(self.inc, self.cfg, self.now))
        self.inc["alerted_at"] = "2026-11-08T10:00:00Z"
        self.assertTrue(intensity_update_due(self.inc, self.cfg, self.now))


class IntensityMessageTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"local_time": {"offset_hours": 3, "name": "بغداد"}, "app_url": ""}
        self.inc = {"verdict": "مؤكد", "alerted_verdict": "مؤكد", "headline": "حريق في البحر",
                    "reason": "رصده قمران مختلفان", "first": "2026-11-08T11:00:00Z",
                    "last": "2026-11-08T11:55:00Z", "alerted_max_frp": 51, "max_frp": 164,
                    "frp_series": [["2026-11-08T11:50:00Z", 164, "mtg"]]}

    def test_intensity_only_update_does_not_claim_new_confirmation(self):
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True)
        self.assertIn("ارتفاع شدة الحريق", message)
        self.assertNotIn("مؤكد الآن", message)
        self.assertIn("من 51 MW إلى 164 MW", message)
        self.assertIn("أعلى شدة مرصودة", message)
        self.assertIn("14:50", message)

    def test_peak_and_lower_latest_reading_are_labeled_with_their_times(self):
        self.inc["max_frp"] = 181
        self.inc["frp_series"].insert(0, ["2026-11-08T11:30:00Z", 181, "mtg"])
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True)
        self.assertIn("أعلى شدة مرصودة:</b> 181 MW · 14:30", message)
        self.assertIn("آخر قراءة:</b> 164 MW · 14:50", message)
        self.assertNotIn("إلى 164 MW", message)

    def test_simultaneous_verdict_and_intensity_use_one_combined_title(self):
        self.inc["alerted_verdict"] = "مرجّح"
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True)
        self.assertIn("الحريق مؤكد الآن وارتفعت شدته", message)
        self.assertIn(self.inc["reason"], message)
        self.assertEqual(message.count("🔄"), 1)

    def test_verdict_only_update_keeps_confirmation_title(self):
        self.inc["alerted_verdict"] = "مرجّح"
        message = alert_message(self.inc, self.cfg, update=True, threaded=True)
        self.assertIn("الحريق مؤكد الآن", message)
        self.assertNotIn("ارتفاع شدة الحريق", message)

    def test_generic_update_for_already_confirmed_incident_has_neutral_title(self):
        message = alert_message(self.inc, self.cfg, update=True, threaded=True)
        self.assertIn("<b>تحديث</b>", message)
        self.assertNotIn("مؤكد الآن", message)

    def test_later_video_update_does_not_repeat_confirmation_or_intensity_claim(self):
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, video_update=True)
        self.assertIn("فيديو محدث للحريق", message)
        self.assertNotIn("مؤكد الآن", message)
        self.assertNotIn("ارتفاع شدة الحريق", message)
        self.assertNotIn("منذ آخر تنبيه", message)
        self.assertIn("أعلى شدة مرصودة:</b> 164 MW", message)

    def test_later_video_update_keeps_peak_and_latest_measurement_labels(self):
        self.inc["max_frp"] = 181
        self.inc["frp_series"].insert(0, ["2026-11-08T11:30:00Z", 181, "mtg"])
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, video_update=True)
        self.assertIn("أعلى شدة مرصودة:</b> 181 MW · 14:30", message)
        self.assertIn("آخر قراءة:</b> 164 MW · 14:50", message)

    def test_unknown_or_higher_previous_peak_does_not_invent_an_increase(self):
        for previous in (None, 181):
            with self.subTest(previous=previous):
                self.inc["alerted_max_frp"] = previous
                message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True)
                self.assertNotIn("منذ آخر تنبيه", message)

    def test_delayed_intensity_update_labels_prior_measurements_with_full_dates(self):
        self.inc.update(first="2026-11-07T22:09:00Z", last="2026-11-07T23:05:00Z",
                        alerted_max_frp=2.41, max_frp=13.39,
                        frp_series=[["2026-11-07T22:09:00Z", 2.4, "viirs"],
                                    ["2026-11-07T23:05:00Z", 13.4, "viirs"]])
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True, delayed=True)

        self.assertIn("تحديث متأخر لقياسات الحريق", message)
        self.assertIn("2026-11-08 الساعة 02:05 (بغداد)", message)
        self.assertIn("من 2.4 MW إلى 13 MW", message)
        self.assertIn("القياسات المذكورة سابقة وليست رصداً جديداً الآن", message)
        self.assertNotIn("مؤكد الآن", message)

    def test_delayed_update_dates_both_peak_and_lower_latest_reading(self):
        self.inc["max_frp"] = 181
        self.inc["frp_series"].insert(0, ["2026-11-08T11:30:00Z", 181, "mtg"])
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, intensity=True, delayed=True)

        self.assertIn("أعلى شدة مرصودة:</b> 181 MW · 2026-11-08 الساعة 14:30 (بغداد)", message)
        self.assertIn("آخر قراءة:</b> 164 MW · 2026-11-08 الساعة 14:50 (بغداد)", message)

    def test_delayed_video_update_does_not_announce_a_new_fire_or_new_confirmation(self):
        self.inc["alerted_verdict"] = "مرجّح"
        message = alert_message(self.inc, self.cfg, update=True, threaded=True, video_update=True, delayed=True)

        self.assertIn("فيديو محدث لقياسات سابقة للحريق", message)
        self.assertIn("2026-11-08 الساعة 14:50 (بغداد)", message)
        self.assertIn("القياسات المذكورة سابقة وليست رصداً جديداً الآن", message)
        self.assertNotIn("مؤكد الآن", message)
        self.assertNotIn("منذ آخر تنبيه", message)


if __name__ == "__main__":
    unittest.main()
