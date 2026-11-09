"""Recovery publishes timestamp-validated source evidence, never daily aggregates."""
import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

from rasad import backfill
from rasad.brain import Memory
from rasad.util import UTC, iso, parse_time


class BackfillTests(unittest.TestCase):
    NOW = parse_time("2026-11-08T21:00:00Z")
    LAT, LON = 25.9239, 49.6935

    def setUp(self):
        self.cfg = {
            "recovery": {"enabled": True, "budget_s": 30, "max_images_per_run": 2,
                         "retry_min": 10, "auto_targets": False,
                         "targets": [{"id": "reviewed", "lat": self.LAT, "lon": self.LON,
                                      "start": "2026-11-08T01:20:00Z",
                                      "end": "2026-11-08T02:00:00Z", "layer": "mtg_frp"}]},
            "memory": {"window_days": 30, "unusual_add_mw": 25,
                       "unusual_ratio": 5, "unusual_facility_km": 3},
            "incidents": {"keep_days": 7},
            "eumetview": {"fire_layers": [{"id": "mtg_frp", "name": "mtg_fd:frp",
                                            "family": "mtg", "cadence_min": 10}]},
        }
        self.state = {}
        self.detect = Mock(return_value=[])
        self.fetch = Mock()
        self.geo = Mock()
        self.geo.facility_within.return_value = True

    def collect(self, now=None, memory=None):
        return backfill.collect(self.cfg, Mock(), self.state, memory, self.geo,
                                now or self.NOW, detect=self.detect, fetch=self.fetch)

    def detection(self, when, power=None, **extra):
        return {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": iso(when),
                "lat": self.LAT, "lon": self.LON, "frp": power,
                "_fi": ["query-placeholder"], **extra}

    def one_slot(self):
        self.cfg["recovery"]["targets"][0]["end"] = "2026-11-08T01:20:00Z"

    def test_disabled_recovery_makes_no_source_requests(self):
        self.cfg["recovery"]["enabled"] = False
        rows, status = self.collect()
        self.assertEqual(rows, [])
        self.assertFalse(status["enabled"])
        self.detect.assert_not_called()
        self.fetch.assert_not_called()

    def test_short_cycles_resume_without_repeating_completed_blank_images(self):
        counts = []
        pending = []
        for _ in range(4):
            rows, status = self.collect()
            self.assertEqual(rows, [])
            counts.append(status["checked"])
            pending.append(status["pending"])
        self.assertEqual(counts, [2, 2, 1, 0])
        self.assertEqual(pending, [3, 1, 0, 0])
        times = [call.args[3] for call in self.detect.call_args_list]
        self.assertEqual(times, [parse_time("2026-11-08T01:20:00Z") + dt.timedelta(minutes=10*i)
                                 for i in range(5)])
        self.fetch.assert_not_called()

    def test_missing_image_stays_pending_and_respects_retry_interval(self):
        self.one_slot()
        self.detect.return_value = None
        rows, status = self.collect()
        self.assertEqual((rows, status["missing"], status["pending"]), ([], 1, 1))
        self.assertEqual((status["images_fetched"], status["images_reused"]), (1, 0))
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["images"], {})
        self.collect(now=self.NOW + dt.timedelta(minutes=9))
        self.assertEqual(self.detect.call_count, 1)
        self.detect.return_value = []
        rows, status = self.collect(now=self.NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.detect.call_count, 2)
        self.assertEqual(status["pending"], 0)

    def test_missing_strength_cannot_be_replaced_by_daily_memory_peak(self):
        self.one_slot()
        self.detect.side_effect = lambda wms, layer, tile, when: [self.detection(when)]
        memory = Memory({})
        memory.record(self.LAT, self.LON, "2026-11-08", 999)
        rows, status = self.collect(memory=memory)
        self.assertEqual(rows, [])
        self.assertEqual((status["missing"], status["pending"]), (1, 1))
        self.fetch.assert_called_once()
        self.assertEqual(memory.cells[Memory.key(self.LAT, self.LON)]["2026-11-08"], 999)

    def test_actual_strength_is_returned_with_historical_no_alert_flags(self):
        self.one_slot()
        self.detect.side_effect = lambda wms, layer, tile, when: [self.detection(when)]
        self.fetch.side_effect = lambda wms, det: det.update(frp=63.19)
        rows, status = self.collect()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frp"], 63.19)
        self.assertEqual(rows[0]["t"], "2026-11-08T01:20:00Z")
        self.assertTrue(rows[0]["_recovered"])
        self.assertTrue(rows[0]["_no_alert"])
        self.assertNotIn("_fi", rows[0])
        self.assertEqual((status["detections"], status["pending"]), (1, 0))

    def test_substituted_timestamp_is_rejected_and_slot_remains_pending(self):
        self.one_slot()
        self.detect.side_effect = lambda wms, layer, tile, when: [
            self.detection(when + dt.timedelta(minutes=10), 63.19)]
        rows, status = self.collect()
        self.assertEqual(rows, [])
        self.assertEqual((status["missing"], status["pending"]), (1, 1))
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["images"], {})
        self.detect.side_effect = lambda wms, layer, tile, when: [self.detection(when, 63.19)]
        rows, status = self.collect(now=self.NOW + dt.timedelta(minutes=10))
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.detect.call_count, 2)
        self.assertEqual(status["images_reused"], 0)

    def test_invalid_or_negative_power_does_not_become_evidence(self):
        self.one_slot()
        for power in (None, -1, float("nan"), float("inf"), True, "invalid"):
            with self.subTest(power=power):
                self.state = {}
                self.detect.side_effect = lambda wms, layer, tile, when: [self.detection(when, power)]
                rows, status = self.collect()
                self.assertEqual(rows, [])
                self.assertEqual(status["pending"], 1)

    def test_expired_future_or_unofficial_targets_make_no_requests(self):
        for changes in (
            {"start": "2026-10-01T01:20:00Z", "end": "2026-10-01T02:00:00Z"},
            {"start": "2026-11-09T01:20:00Z", "end": "2026-11-09T02:00:00Z"},
            {"layer": "mtg_day_spot"},
        ):
            with self.subTest(changes=changes):
                cfg = copy.deepcopy(self.cfg)
                cfg["recovery"]["targets"][0].update(changes)
                rows, status = backfill.collect(cfg, Mock(), {}, None, self.geo, self.NOW,
                                                detect=self.detect, fetch=self.fetch)
                self.assertEqual(rows, [])
                self.assertEqual(status["invalid_targets"], 1)
        self.detect.assert_not_called()

    def test_automatic_daily_peak_selects_a_search_but_never_a_detection(self):
        self.cfg["recovery"].update(targets=[], auto_targets=True)
        memory = Memory({})
        for ago in range(1, 6):
            memory.record(self.LAT, self.LON, (self.NOW.date()-dt.timedelta(days=ago)).isoformat(), 1.25)
        memory.record(self.LAT, self.LON, self.NOW.date().isoformat(), 80.28)
        before = copy.deepcopy(memory.cells)
        rows, status = self.collect(memory=memory)
        self.assertEqual(rows, [])
        self.assertEqual(status["targets"], 1)
        self.assertEqual(status["checked"], 2)
        self.assertEqual(memory.cells, before)
        self.fetch.assert_not_called()

    def test_automatic_peak_away_from_a_facility_is_not_replayed(self):
        self.cfg["recovery"].update(targets=[], auto_targets=True)
        memory = Memory({})
        for ago in range(1, 6):
            memory.record(self.LAT, self.LON, (self.NOW.date()-dt.timedelta(days=ago)).isoformat(), 1.25)
        memory.record(self.LAT, self.LON, self.NOW.date().isoformat(), 80.28)
        self.geo.facility_within.return_value = False
        rows, status = self.collect(memory=memory)
        self.assertEqual((rows, status["targets"]), ([], 0))
        self.detect.assert_not_called()

    def test_budget_zero_leaves_all_slots_pending(self):
        self.cfg["recovery"]["budget_s"] = 0
        rows, status = self.collect()
        self.assertEqual((rows, status["checked"], status["pending"]), ([], 0, 5))
        self.detect.assert_not_called()

    def test_feature_limit_resumes_remaining_symbols_before_completing_slot(self):
        self.one_slot()
        self.cfg["recovery"]["max_features_per_image"] = 1
        self.detect.side_effect = lambda wms, layer, tile, when: [
            self.detection(when, None),
            self.detection(when, None, lat=self.LAT+0.01),
            self.detection(when, None, lat=self.LAT+0.02)]
        self.fetch.side_effect = lambda wms, det: det.update(frp=63.19)
        found = []
        pending = []
        for step in range(3):
            rows, status = self.collect(now=self.NOW+dt.timedelta(minutes=10*step))
            found.extend(rows)
            pending.append(status["pending"])
            self.assertEqual(status["missing"], 0, "a reached work cap is not absent source evidence")
            self.assertEqual(status["deferred"], 1 if step < 2 else 0)
        self.assertEqual(pending, [1, 1, 0])
        self.assertEqual([row["lat"] for row in found], [self.LAT, self.LAT+0.01, self.LAT+0.02])
        self.assertEqual(self.fetch.call_count, 3)
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["features"], {})

    def test_missing_strength_is_not_added_to_feature_cursor(self):
        self.one_slot()
        self.cfg["recovery"]["max_features_per_image"] = 1
        self.detect.side_effect = lambda wms, layer, tile, when: [self.detection(when)]
        self.collect()
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["features"]["2026-11-08T01:20:00Z"], [])
        self.fetch.side_effect = lambda wms, det: det.update(frp=63.19)
        rows, status = self.collect(now=self.NOW+dt.timedelta(minutes=10))
        self.assertEqual(len(rows), 1)
        self.assertEqual(status["pending"], 0)
        self.assertEqual(self.fetch.call_count, 2)

    def test_unavailable_first_symbol_does_not_starve_neighbouring_symbols(self):
        self.one_slot()
        self.cfg["recovery"]["max_features_per_image"] = 1
        self.detect.side_effect = lambda wms, layer, tile, when: [
            self.detection(when, None), self.detection(when, None, lat=self.LAT+0.01)]

        def fetch(wms, det):
            if det["lat"] != self.LAT:
                det["frp"] = 44.45

        self.fetch.side_effect = fetch
        rows, _ = self.collect()
        self.assertEqual(rows, [])
        rows, status = self.collect(now=self.NOW+dt.timedelta(minutes=10))
        self.assertEqual([row["frp"] for row in rows], [44.45])
        self.assertEqual(status["pending"], 1)

    def test_neighbour_outside_search_radius_is_not_associated(self):
        self.one_slot()
        self.detect.side_effect = lambda wms, layer, tile, when: [
            self.detection(when, 63.19, lat=self.LAT+1)]
        rows, status = self.collect()
        self.assertEqual((rows, status["pending"]), ([], 0))
        self.fetch.assert_not_called()

    def test_slow_verified_image_resumes_next_cycle_without_another_download(self):
        self.one_slot()
        self.cfg["recovery"]["budget_s"] = 15
        clock = [0.0]

        def slow_image(wms, layer, tile, when):
            clock[0] += 15
            return [self.detection(when)]

        self.detect.side_effect = slow_image
        self.fetch.side_effect = lambda wms, det: det.update(frp=63.19)
        with patch.object(backfill.time, "monotonic", side_effect=lambda: clock[0]):
            rows, status = self.collect()
            self.assertEqual(rows, [])
            self.assertEqual((status["images_fetched"], status["images_reused"]), (1, 0))
            self.assertEqual((status["deferred"], status["missing"]), (1, 0))
            self.fetch.assert_not_called()
            # A cycle cap, unlike unavailable source evidence, needs no ten-minute backoff.
            rows, status = self.collect(now=self.NOW + dt.timedelta(minutes=2))
        self.detect.assert_called_once()
        self.fetch.assert_called_once()
        self.assertEqual([row["frp"] for row in rows], [63.19])
        self.assertEqual((status["images_fetched"], status["images_reused"]), (0, 1))
        self.assertEqual(status["pending"], 0)
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["images"], {})

    def test_failed_strength_query_reuses_original_feature_metadata_after_backoff(self):
        self.one_slot()
        when = parse_time("2026-11-08T01:20:00Z")
        original_fi = ["mtg_fd:frp", [49, 25, 50, 26], 400, 334, 186.5, 169, iso(when)]
        self.detect.return_value = [self.detection(when, _fi=copy.deepcopy(original_fi))]

        def unavailable(wms, det):
            fi = det.pop("_fi")
            fi[1][0] = -999  # A query adapter cannot mutate the saved original coordinates.

        self.fetch.side_effect = unavailable
        rows, status = self.collect()
        self.assertEqual((rows, status["pending"]), ([], 1))
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        self.assertEqual(progress["images"][iso(when)]["symbols"][0]["original_fi"], original_fi)
        self.collect(now=self.NOW + dt.timedelta(minutes=9))
        self.assertEqual(self.fetch.call_count, 1)

        def available(wms, det):
            self.assertEqual(det.pop("_fi"), original_fi)
            det["frp"] = 80.28

        self.fetch.side_effect = available
        rows, status = self.collect(now=self.NOW + dt.timedelta(minutes=10))
        self.detect.assert_called_once()
        self.assertEqual([row["frp"] for row in rows], [80.28])
        self.assertEqual(status["images_reused"], 1)
        self.assertEqual(progress["images"], {})

    def test_invalid_cached_timestamp_requires_another_verified_image(self):
        self.one_slot()
        self.cfg["recovery"]["max_features_per_image"] = 1
        self.detect.side_effect = lambda wms, layer, tile, when: [
            self.detection(when, None), self.detection(when, None, lat=self.LAT + 0.01)]
        self.fetch.side_effect = lambda wms, det: det.update(frp=63.19)
        self.collect()
        progress = next(iter(self.state["facility_recovery"]["targets"].values()))
        slot = "2026-11-08T01:20:00Z"
        progress["images"][slot]["symbols"][1]["t"] = "2026-11-08T01:30:00Z"
        rows, status = self.collect(now=self.NOW + dt.timedelta(minutes=2))
        self.assertEqual(self.detect.call_count, 2)
        self.assertEqual((status["images_fetched"], status["images_reused"]), (1, 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(status["pending"], 0)
        self.assertEqual(progress["images"], {})


if __name__ == "__main__":
    unittest.main()
