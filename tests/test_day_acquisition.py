"""Daylight comparisons must make useful progress during short, unreliable scans."""
import copy
from contextlib import ExitStack
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from rasad import daywatch, engine, seawatch
from rasad.util import UTC, iso


class RasterService:
    """Return full, correctly geolocated frames, with optional missing acquisitions."""

    url = "https://example.test/wms"

    def __init__(self, missing=None):
        self.calls = []
        self.missing = missing or (lambda layer, when: False)

    def getmap(self, layer, bbox, width, height, *, t, **kwargs):
        self.calls.append((layer, tuple(bbox), width, height, t))
        if self.missing(layer, t):
            return None, None
        values = [90, 30, 6, 255] if layer == "fire-rgb" else [30, 30, 30, 255]
        raw = np.full((height, width, 4), values, dtype=np.uint8)
        return Image.fromarray(raw), t


class DayAcquisitionTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 13, 6, 20, tzinfo=UTC)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = RasterService()

    def config(self, *, live=True, boxes=1):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg.update(_background_media=live, _state_dir=self.root / "state")
        cfg["seawatch"].update({
            "layer": "fire-rgb", "check_layer": "ir-check",
            "boxes": [{"id": f"box{i}", "bbox": [57.95 + i * .1, 24.38,
                                                       58.02 + i * .1, 24.45]}
                      for i in range(boxes)],
            "slots_per_run": 1, "catchup_hours": 0, "delay_min": 0,
            "budget_s": 35,
            "daytime": {"enabled": True, "ref_minutes": [30, 60], "min_sun_elev": 6},
        })
        return cfg

    def watch(self, cfg, state, *, finder=None, now=None):
        availability = Mock()
        availability.has.side_effect = AssertionError("daylight must acquire useful rasters directly")
        with ExitStack() as stack:
            stack.enter_context(patch.object(seawatch, "_night", return_value=[False]))
            stack.enter_context(patch.object(seawatch, "sun_elevation", return_value=40.0))
            if finder is not None:
                stack.enter_context(patch.object(daywatch, "find_spots", finder))
            result = seawatch.watch(cfg, self.service, state, now or self.NOW,
                                    avail=availability)
        availability.has.assert_not_called()
        return result

    def test_daylight_acquires_six_useful_frames_without_availability_probes(self):
        cfg = self.config()
        state = {}
        finder = Mock(return_value=([], {}))
        self.assertEqual(self.watch(cfg, state, finder=finder), [])
        finder.assert_called_once()
        self.assertEqual(len(self.service.calls), 6)
        self.assertTrue(all(call[2] >= 50 and call[3] >= 50 for call in self.service.calls))
        self.assertIn("day:box0|" + iso(self.NOW), state["seawatch"])
        self.assertEqual(state["sea_day_status"]["checked"], 1)

    def test_real_day_finder_leaves_missing_mandatory_reference_pending(self):
        absent = self.NOW - dt.timedelta(minutes=30)
        self.service.missing = lambda layer, when: layer == "ir-check" and when == absent
        state = {}
        self.assertEqual(self.watch(self.config(), state), [])
        self.assertNotIn("day:box0|" + iso(self.NOW), state["seawatch"])
        self.assertEqual(state["sea_day_status"]["checked"], 0)
        self.assertGreaterEqual(state["sea_day_status"]["missing"], 1)
        self.assertEqual(len(self.service.calls), 6, "one attempt for each useful raster")

    def test_live_scan_tries_each_missing_raster_once(self):
        self.service.missing = lambda layer, when: True
        state = {}
        finder = Mock(return_value=(None, "pictures missing"))
        self.watch(self.config(), state, finder=finder)
        self.assertEqual(len(self.service.calls), 6)
        self.assertEqual(len(set(self.service.calls)), 6)
        self.assertEqual(state["seawatch"], {})

    def test_finished_prefetch_is_evaluated_even_when_the_download_budget_ends(self):
        cfg = self.config()
        state = {}
        clock = [1_000.0]
        original = seawatch.Pictures.prefetch

        def prefetch(pictures, requests):
            original(pictures, requests)
            clock[0] += 36

        finder = Mock(wraps=daywatch.find_spots)
        with patch.object(seawatch.time, "time", side_effect=lambda: clock[0]), \
                patch.object(seawatch.Pictures, "prefetch", prefetch):
            self.watch(cfg, state, finder=finder)
        finder.assert_called_once()
        self.assertEqual(state["sea_day_status"]["checked"], 1)
        self.assertIn("day:box0|" + iso(self.NOW), state["seawatch"])

    def test_incomplete_comparison_reuses_acquired_frames_when_latest_advances(self):
        cfg = self.config()
        cfg["seawatch"]["catchup_hours"] = 1
        absent = self.NOW - dt.timedelta(minutes=30)
        self.service.missing = lambda layer, when: layer == "ir-check" and when == absent
        state = {}
        self.watch(cfg, state)
        self.assertEqual(len(self.service.calls), 6)
        self.assertNotIn("day:box0|" + iso(self.NOW), state["seawatch"])

        self.service.calls.clear()
        self.service.missing = lambda layer, when: False
        self.watch(cfg, state, now=self.NOW + dt.timedelta(minutes=10))
        self.assertEqual(len(self.service.calls), 1, "only the missing reference needs another download")
        self.assertEqual((self.service.calls[0][0], self.service.calls[0][-1]),
                         ("ir-check", absent))
        self.assertIn("day:box0|" + iso(self.NOW), state["seawatch"])
        self.assertNotIn("day:box0|" + iso(self.NOW + dt.timedelta(minutes=10)),
                         state["seawatch"])

    def test_day_validation_cannot_start_additional_downloads(self):
        cfg = self.config()
        state = {}

        def finder(config, pictures, box, when, *args, **kwargs):
            before = len(self.service.calls)
            result = pictures.get("not-prefetched", box["bbox"], 50, 50, when)
            self.assertIsNone(result)
            self.assertEqual(len(self.service.calls), before)
            return None, "required image unavailable"

        self.watch(cfg, state, finder=finder)
        self.assertEqual(len(self.service.calls), 6)
        self.assertEqual(state["seawatch"], {})

    def test_incomplete_slot_resumes_after_newest_time_advances_then_releases(self):
        cfg = self.config()
        cfg["seawatch"]["catchup_hours"] = 1
        state = {}
        attempted = []

        def finder(config, pictures, box, when, *args, **kwargs):
            attempted.append(when)
            return None, "reference not ready"

        for i in range(5):
            self.watch(cfg, state, finder=finder, now=self.NOW + dt.timedelta(minutes=10 * i))
        self.assertEqual(attempted[:2], [self.NOW, self.NOW], "resume the interrupted comparison")
        self.assertLessEqual(attempted.count(self.NOW), 4, "a permanent gap cannot trap all future scans")
        self.assertTrue(any(when > self.NOW for when in attempted))
        self.assertEqual(state["seawatch"], {})

    def test_live_box_order_rotates_across_scans(self):
        cfg = self.config(boxes=2)
        state = {}
        starts = []
        current = []

        def finder(config, pictures, box, when, *args, **kwargs):
            current.append(box["id"])
            return None, "reference not ready"

        for _ in range(2):
            current.clear()
            self.watch(cfg, state, finder=finder)
            self.assertTrue(current)
            starts.append(current[0])
        self.assertEqual(set(starts), {"box0", "box1"})

    def test_zero_budget_keeps_an_incomplete_slot_without_attempting_it(self):
        cfg = self.config()
        cfg["seawatch"]["catchup_hours"] = 1
        state = {}
        missing = Mock(return_value=(None, "reference not ready"))
        self.watch(cfg, state, finder=missing)
        pending = copy.deepcopy(state["sea_day_resume"])
        before = len(self.service.calls)

        cfg["seawatch"]["budget_s"] = -1
        finder = Mock(return_value=([], {}))
        self.watch(cfg, state, finder=finder, now=self.NOW + dt.timedelta(minutes=10))
        finder.assert_not_called()
        self.assertEqual(len(self.service.calls), before)
        self.assertEqual(state["sea_day_resume"], pending)
        self.assertEqual(state["seawatch"], {})


class PicturesAcquisitionTests(unittest.TestCase):
    WHEN = DayAcquisitionTests.NOW

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.service = RasterService()
        self.args = ("fire-rgb", [57.9, 24.3, 58.0, 24.4], 50, 50, self.WHEN)

    def test_cached_only_miss_does_not_fetch_and_restores_normal_access(self):
        pictures = seawatch.Pictures(self.service)
        with pictures.cached_only():
            self.assertIsNone(pictures.get(*self.args, 0))
        self.assertEqual(self.service.calls, [])
        result = pictures.get(*self.args, 0)
        self.assertEqual(result[0, 0], 90)
        self.assertEqual(len(self.service.calls), 1)

    def test_cached_only_can_read_a_complete_persisted_raster(self):
        seawatch.Pictures(self.service, cache_dir=self.folder).get(*self.args, 0)
        self.service.calls.clear()
        self.service.missing = lambda layer, when: True
        pictures = seawatch.Pictures(self.service, cache_dir=self.folder)
        with pictures.cached_only():
            red = pictures.get(*self.args, 0)
            blue = pictures.get(*self.args, 2)
        self.assertEqual((red[0, 0], blue[0, 0]), (90, 6))
        self.assertEqual(self.service.calls, [])

    def test_default_retains_two_attempts_for_a_transient_failure(self):
        failed = [False]

        def missing(layer, when):
            if not failed[0]:
                failed[0] = True
                return True
            return False

        self.service.missing = missing
        result = seawatch.Pictures(self.service).get(*self.args, 0)
        self.assertEqual(result[0, 0], 90)
        self.assertEqual(len(self.service.calls), 2)

    def test_single_attempt_failure_remains_retryable_in_the_next_cycle(self):
        self.service.missing = lambda layer, when: True
        pictures = seawatch.Pictures(self.service, attempts=1, cache_dir=self.folder)
        self.assertIsNone(pictures.get(*self.args, 0))
        self.assertEqual(len(self.service.calls), 1)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.service.missing = lambda layer, when: False
        result = seawatch.Pictures(self.service, attempts=1, cache_dir=self.folder).get(*self.args, 0)
        self.assertEqual(result[0, 0], 90)
        self.assertEqual(len(self.service.calls), 2)


if __name__ == "__main__":
    unittest.main()
