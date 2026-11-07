"""Offline regressions for independent hot spots near offshore platforms."""
import copy
import datetime as dt
import math
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rasad import engine, seawatch
from rasad.brain import Memory
from rasad.geodb import Geo, GeoIndex
from rasad.util import box_around, floor_time, haversine_km, iso, px_to_latlon


class PlatformRadiusTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 11, 7, 22, 30, tzinfo=dt.timezone.utc)

    def find(self, platform_distance, radius=None, known_heat=False):
        cfg = copy.deepcopy(engine.DEFAULTS)
        if radius is not None:
            cfg["seawatch"]["platform_km"] = radius
        box = {"id": "test", "bbox": box_around(26.165, 56.071, 12.5)}
        width, height = seawatch.box_size(box, cfg["seawatch"]["km_per_px"])
        row, col = height // 2, width // 2
        lat, lon = px_to_latlon(box["bbox"], width, height, col, row)
        current = np.full((height, width), 30.0, dtype=np.float32)
        previous = current.copy()
        current[row, col] = 100.0
        pics = Mock()
        pics.get.side_effect = lambda layer, bbox, w, h, t: current if t == self.WHEN else previous

        # Use the real platform spatial lookup, with one platform at a known distance.
        platform_lat = lat + math.degrees(platform_distance / 6371.0)
        geo = Geo.__new__(Geo)
        geo.fac = GeoIndex([[platform_lat, lon, "Test platform", "منصة نفطية", "platform"]])
        self.assertAlmostEqual(haversine_km(lat, lon, platform_lat, lon), platform_distance, places=6)
        memory = Memory({})
        if known_heat:
            day = (self.WHEN - dt.timedelta(days=1)).date().isoformat()
            memory.record(lat, lon, day, 10.0)
        sea_mask = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
        with patch.dict(seawatch._SEA, {}, clear=True), \
                patch.object(seawatch, "land_mask_points", return_value=sea_mask), \
                patch.object(seawatch, "sun_elevation", return_value=-30.0), \
                patch.object(seawatch, "_ir105_change", return_value=0.7):
            return seawatch.find_spots(cfg, pics, box, self.WHEN, mem=memory, geo=geo)

    def test_independent_spot_nine_km_from_platform_is_kept(self):
        spots, notes = self.find(8.9)
        self.assertEqual(len(spots), 1)
        self.assertEqual(notes["rejected"], {})
        self.assertGreater(spots[0]["above_sea"], 3.0)

    def test_heat_two_km_from_platform_stays_excluded(self):
        spots, notes = self.find(2.0)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"oil platform": 1})

    def test_known_heat_stays_excluded_with_smaller_platform_radius(self):
        spots, notes = self.find(8.9, known_heat=True)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"heat seen here before": 1})

    def test_explicit_ten_km_radius_is_respected(self):
        spots, notes = self.find(8.9, radius=10.0)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"oil platform": 1})


class PlatformRadiusCacheTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 7, 23, tzinfo=dt.timezone.utc)

    def watch(self, old_radius):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"]["boxes"] = [{"id": "test", "bbox": [55, 25, 56, 26]}]
        cfg["seawatch"]["slots_per_run"] = 1
        cfg["seawatch"]["catchup_hours"] = 0
        latest = floor_time(self.NOW - dt.timedelta(minutes=cfg["seawatch"]["delay_min"]), 10)
        latest_key = "test|" + iso(latest)
        older = iso(latest - dt.timedelta(hours=1))
        old_done = {latest_key: iso(latest), "other|" + older: older}
        candidate = {"t": iso(latest), "lat": 26.0, "lon": 56.0, "rise": 12.0}
        history = {"520,1120": ["2026-11-06"]}
        general_slots = {"unrelated": "keep"}
        state = {"seawatch": old_done.copy(), "sea_candidates": [candidate.copy()],
                 "sea_sparkle": copy.deepcopy(history), "slots": general_slots}
        if old_radius is not None:
            state["sea_platform_km"] = old_radius
        avail = Mock()
        avail.has.return_value = True
        with patch.object(seawatch, "Pictures"), \
                patch.object(seawatch, "_night", return_value=[True]), \
                patch.object(seawatch, "find_spots", return_value=([], {})) as find:
            result = seawatch.watch(cfg, Mock(), state, self.NOW, avail=avail)
        self.assertEqual(result, [])
        self.assertEqual(state["sea_candidates"], [candidate])
        self.assertEqual(state["sea_sparkle"], history)
        self.assertIs(state["slots"], general_slots)
        self.assertEqual(state["sea_platform_km"], 3.0)
        return state, old_done, latest_key, find

    def test_changed_radius_rechecks_completed_slots(self):
        state, _, latest_key, find = self.watch(10.0)
        find.assert_called_once()
        self.assertEqual(state["seawatch"], {latest_key: latest_key.split("|", 1)[1]})

    def test_unchanged_radius_preserves_completed_slots(self):
        state, old_done, _, find = self.watch(3.0)
        find.assert_not_called()
        self.assertEqual(state["seawatch"], old_done)

    def test_legacy_state_without_radius_marker_is_rechecked(self):
        state, _, latest_key, find = self.watch(None)
        find.assert_called_once()
        self.assertEqual(set(state["seawatch"]), {latest_key})


if __name__ == "__main__":
    unittest.main()
