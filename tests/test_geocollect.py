"""Collection must stay bounded while making progress over all sources and tiles."""
from contextlib import contextmanager
import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

from rasad import geocollect
from rasad.util import UTC, iso


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class BudgetWMS:
    def __init__(self):
        self.entered = []
        self.active = False

    @contextmanager
    def budget(self, seconds, timeout_s):
        self.entered.append((seconds, timeout_s))
        self.active = True
        try:
            yield self
        finally:
            self.active = False


class GeoCollectTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)
    NEWEST = NOW - dt.timedelta(minutes=10)

    def setUp(self):
        self.cfg = {"eumetview": {
            "fire_layers": [
                {"id": "a", "name": "a:fire", "family": "a", "cadence_min": 10,
                 "clock_layer": "a:clock", "label_ar": "A"},
                {"id": "b", "name": "b:fire", "family": "b", "cadence_min": 10},
            ],
            "tile_px": 1000, "lookback_slots": 2, "settle_minutes": 25,
            "catchup_hours": 2, "catchup_budget_s": 75,
            "scan_budget_s": 90, "scan_max_images": 12, "scan_workers": 4,
        }}
        self.tiles = [[float(i), 20, float(i + 1), 21] for i in range(16)]
        self.state = {}
        self.availability = Mock()
        self.availability.has.return_value = True
        self.latest = Mock(return_value=self.NEWEST)
        self.detect = Mock(return_value=[])

    def collect(self, **kwargs):
        return geocollect.collect(self.cfg, kwargs.pop("wms", Mock()), self.state,
                                  kwargs.pop("now", self.NOW), kwargs.pop("tiles", self.tiles),
                                  self.availability, detect=self.detect, latest=self.latest, **kwargs)

    def test_no_sources_or_tiles_does_no_requests(self):
        self.assertEqual(self.collect(tiles=[]), ([], {}, 0))
        self.cfg["eumetview"]["fire_layers"] = []
        self.assertEqual(self.collect(), ([], {}, 0))
        self.latest.assert_not_called()
        self.detect.assert_not_called()

    def test_latest_tiles_cover_both_sources_before_older_images(self):
        wms = BudgetWMS()
        _, status, count = self.collect(wms=wms)
        self.assertEqual(count, 12)
        self.assertEqual(wms.entered, [(90, 20)])
        self.assertFalse(wms.active)
        self.assertEqual({call.args[1]["id"] for call in self.detect.call_args_list}, {"a", "b"})
        self.assertEqual({call.args[3] for call in self.detect.call_args_list}, {self.NEWEST})
        self.assertEqual((status["a"]["checked"], status["b"]["checked"]), (6, 6))
        self.assertTrue(status["a"]["budget_exhausted"])
        self.assertEqual(status["a"]["pending_recent"], 26)

    def test_rotating_cursor_completes_every_newest_tile_without_duplicates(self):
        for _ in range(3):
            self.collect()
        newest = [(call.args[1]["id"], tuple(call.args[2])) for call in self.detect.call_args_list
                  if call.args[3] == self.NEWEST]
        self.assertEqual(len(newest), 32)
        self.assertEqual(len(set(newest)), 32)
        self.assertEqual({tile for _, tile in newest}, {tuple(tile) for tile in self.tiles})

    def test_one_image_limit_does_not_always_spend_on_first_source(self):
        self.cfg["eumetview"]["scan_max_images"] = 1
        self.collect()
        self.collect()
        self.assertEqual([call.args[1]["id"] for call in self.detect.call_args_list], ["a", "b"])

    def test_missing_tile_stays_pending_and_can_succeed_later(self):
        self.cfg["eumetview"].update(scan_max_images=1, lookback_slots=1, catchup_hours=0)
        self.cfg["eumetview"]["fire_layers"] = self.cfg["eumetview"]["fire_layers"][:1]
        self.detect.return_value = None
        _, status, count = self.collect(tiles=self.tiles[:1])
        self.assertEqual(count, 1)
        self.assertEqual(self.state["slots"], {})
        self.assertEqual(status["a"]["missing"], 1)
        self.assertEqual(status["a"]["pending_recent"], 1)
        detection = {"src": "a", "t": iso(self.NEWEST)}
        self.detect.return_value = [detection]
        detections, status, count = self.collect(tiles=self.tiles[:1])
        self.assertEqual(detections, [detection])
        self.assertEqual(status["a"]["pending_recent"], 0)
        self.assertTrue(status["a"]["scan_complete"])
        self.assertEqual(len(self.state["slots"]), 1)

    def test_settled_success_is_read_once_more_but_then_cached(self):
        self.cfg["eumetview"].update(scan_max_images=1, lookback_slots=1, catchup_hours=0)
        self.cfg["eumetview"]["fire_layers"] = self.cfg["eumetview"]["fire_layers"][:1]
        self.collect(tiles=self.tiles[:1])
        self.assertEqual(self.detect.call_count, 1)
        self.collect(tiles=self.tiles[:1])
        self.assertEqual(self.detect.call_count, 1)
        self.collect(tiles=self.tiles[:1], now=self.NOW + dt.timedelta(minutes=20))
        self.assertEqual(self.detect.call_count, 2)
        self.collect(tiles=self.tiles[:1], now=self.NOW + dt.timedelta(minutes=30))
        self.assertEqual(self.detect.call_count, 2)

    def test_clock_discovery_is_inside_shared_budget(self):
        clock = Clock()
        self.cfg["eumetview"]["scan_budget_s"] = 5

        def slow_clock(*args):
            clock.value += 6
            return self.NEWEST

        self.latest.side_effect = slow_clock
        with patch.object(geocollect.time, "monotonic", clock):
            _, status, count = self.collect()
        self.latest.assert_called_once()
        self.detect.assert_not_called()
        self.assertEqual(count, 0)
        self.assertTrue(status["a"]["budget_exhausted"])

    def test_recent_images_share_wall_clock_budget_with_catchup(self):
        clock = Clock()
        self.cfg["eumetview"].update(scan_budget_s=5, scan_workers=1, scan_max_images=100)

        def slow_tile(*args):
            clock.value += 3
            return []

        self.detect.side_effect = slow_tile
        with patch.object(geocollect.time, "monotonic", clock):
            _, status, count = self.collect()
        self.assertEqual(count, 2)
        self.assertEqual(self.detect.call_count, 2)
        self.assertEqual(len(self.state["slots"]), 2)
        self.assertEqual({call.args[3] for call in self.detect.call_args_list}, {self.NEWEST})
        self.assertTrue(status["a"]["budget_exhausted"])

    def test_unavailable_older_slot_is_never_marked_read(self):
        self.cfg["eumetview"].update(scan_max_images=20, scan_workers=1)
        self.cfg["eumetview"]["fire_layers"] = self.cfg["eumetview"]["fire_layers"][:1]
        self.availability.has.return_value = False
        _, status, count = self.collect(tiles=self.tiles[:1])
        self.assertEqual(count, 1)
        self.assertEqual(len(self.state["slots"]), 1)
        self.assertEqual(status["a"]["pending_recent"], 1)
        self.assertFalse(status["a"]["scan_complete"])

    def test_zero_budget_or_image_limit_makes_no_clock_or_image_requests(self):
        for changes in ({"scan_budget_s": 0}, {"scan_max_images": 0}):
            with self.subTest(changes=changes):
                cfg = copy.deepcopy(self.cfg)
                cfg["eumetview"].update(changes)
                detections, status, count = geocollect.collect(
                    cfg, Mock(), {}, self.NOW, self.tiles, self.availability,
                    detect=self.detect, latest=self.latest)
                self.assertEqual((detections, count), ([], 0))
                self.assertTrue(all(not row["ok"] for row in status.values()))
        self.latest.assert_not_called()
        self.detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
