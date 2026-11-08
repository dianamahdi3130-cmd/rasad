"""Synthetic regressions for uncalibrated, experimental daylight candidates."""
import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rasad import daywatch, seawatch
from rasad.brain import Memory
from rasad.util import box_around, iso


class DaywatchTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 11, 8, 6, 0, tzinfo=dt.timezone.utc)

    def setUp(self):
        self.cfg = {"seawatch": {"layer": "fire-rgb", "check_layer": "ir105",
                                 "km_per_px": 0.5, "min_coast_km": 3,
                                 "platform_km": 3, "known_heat_km": 10,
                                 "daytime": {}}}
        self.box = {"id": "test", "bbox": box_around(25, 57, 12.5)}
        self.width, self.height = seawatch.box_size(self.box, 0.5)
        self.row, self.col = self.height // 2, self.width // 2
        self.times = [self.WHEN - dt.timedelta(minutes=m) for m in (0, 30, 60)]
        self.data = {}
        for when in self.times:
            for layer, channel, baseline in (("fire-rgb", 0, 30), ("fire-rgb", 2, 2),
                                              ("ir105", None, 50)):
                self.data[(layer, channel, when)] = np.full(
                    (self.height, self.width), baseline, dtype=np.float32)
        self.data[("fire-rgb", 0, self.WHEN)][self.row, self.col] = 100
        self.pics = Mock()
        self.pics.get.side_effect = lambda layer, bbox, w, h, when, channel=None: self.data.get(
            (layer, channel, when))

    @staticmethod
    def geometry(lat, lon, when):
        shape = np.broadcast_shapes(lat.shape, lon.shape)
        return tuple(np.full(shape, v) for v in (30, 25, 60))

    def find(self, *, geometry=None, land=False, sea=None, **kwargs):
        mask = lambda lat, lon: np.full(np.asarray(lat).shape, land, dtype=bool)
        sea = np.ones((self.height, self.width), dtype=bool) if sea is None else sea
        with patch.object(daywatch, "solar_geometry", side_effect=geometry or self.geometry), \
                patch.object(daywatch, "land_mask_points", return_value=mask), \
                patch.object(seawatch, "sea_grid", return_value=sea):
            return daywatch.find_spots(self.cfg, self.pics, self.box, self.WHEN, **kwargs)

    def test_new_red_spot_is_only_an_experimental_candidate(self):
        spots, notes = self.find()
        self.assertEqual(len(spots), 1)
        spot = spots[0]
        self.assertEqual(spot["src"], "mtg_day_spot")
        self.assertIsNone(spot["frp"])
        self.assertTrue(spot["experimental"])
        self.assertTrue(spot["daytime"])
        self.assertEqual(spot["family"], "mtg")
        self.assertEqual(spot["kind"], "geo")
        self.assertEqual(spot["t"], iso(self.WHEN))
        self.assertGreater(spot["rise"], 60)
        self.assertEqual(spot["z10"], 0)
        self.assertEqual(spot["glint_angle"], 60)
        self.assertEqual(notes["units"], "display indices")
        self.assertEqual(notes["references_min"], [30, 60])
        self.assertEqual(notes["rejected"], {})
        self.assertNotIn("temperature", spot)
        self.assertNotIn("MW", str(spot))
        self.assertEqual(self.pics.get.call_count, 9)
        self.assertEqual({c.args[-1] for c in self.pics.get.call_args_list}, {None, 0, 2})

    def test_current_glint_is_rejected_before_fetching(self):
        def geometry(lat, lon, when):
            sun, view, glint = self.geometry(lat, lon, when)
            return sun, view, glint * 0 + 10
        spots, reason = self.find(geometry=geometry)
        self.assertEqual(spots, [])
        self.assertIn("glint", reason)
        self.pics.get.assert_not_called()

    def test_ref_sunrise_and_glint_are_checked_per_pixel(self):
        for bad_reference, kind in ((self.times[1], "sun"), (self.times[2], "glint")):
            with self.subTest(kind=kind):
                def geometry(lat, lon, when):
                    sun, view, glint = self.geometry(lat, lon, when)
                    if when == bad_reference:
                        target = sun if kind == "sun" else glint
                        target[self.row, self.col] = 0
                    return sun, view, glint
                spots, _ = self.find(geometry=geometry)
                self.assertEqual(spots, [])

    def test_low_view_elevation_is_rejected(self):
        def geometry(lat, lon, when):
            sun, view, glint = self.geometry(lat, lon, when)
            return sun, view * 0 + 9, glint
        self.assertEqual(self.find(geometry=geometry)[0], [])

    def test_blue_reflection_rise_is_rejected(self):
        self.data[("fire-rgb", 2, self.WHEN)][self.row, self.col] = 20
        spots, notes = self.find()
        self.assertEqual(spots, [])
        self.assertIn("blue-channel reflection or cloud", notes["rejected"])

    def test_existing_bright_blue_reflection_is_rejected(self):
        for when in self.times:
            self.data[("fire-rgb", 2, when)][self.row, self.col] = 20
        self.assertEqual(self.find()[0], [])

    def test_ir_change_guard_works_without_mistaking_values_for_kelvin(self):
        self.data[("ir105", None, self.WHEN)][self.row, self.col] = 40
        spots, notes = self.find()
        self.assertEqual(spots, [])
        self.assertIn("cloud change (IR105)", notes["rejected"])

    def test_stable_cold_cloud_uses_lower_sea_percentile(self):
        for when in self.times:
            self.data[("ir105", None, when)][self.row - 1:self.row + 2,
                                            self.col - 1:self.col + 2] = 90
        spots, notes = self.find()
        self.assertEqual(spots, [])
        self.assertIn("cold cloud in IR105", notes["rejected"])

    def test_long_warming_band_is_rejected(self):
        self.data[("fire-rgb", 0, self.WHEN)][self.row, :] = 60
        self.data[("fire-rgb", 0, self.WHEN)][self.row, self.col] = 100
        spots, notes = self.find()
        self.assertEqual(spots, [])
        self.assertIn("part of a long warming band", notes["rejected"])

    def test_broad_warming_is_rejected(self):
        self.data[("fire-rgb", 0, self.WHEN)][7:43, 7:43] = 100
        self.assertEqual(self.find()[0], [])

    def test_missing_current_or_reference_ir_requires_retry(self):
        original = copy.deepcopy(self.data)
        for when in self.times:
            with self.subTest(time=when):
                self.data = copy.deepcopy(original)
                del self.data[("ir105", None, when)]
                spots, reason = self.find()
                self.assertIsNone(spots)
                self.assertIn("not ready", reason)

    def test_nonfinite_in_any_core_channel_requires_retry(self):
        original = copy.deepcopy(self.data)
        for key in original:
            with self.subTest(channel=key):
                self.data = copy.deepcopy(original)
                self.data[key][self.row, self.col] = np.nan
                spots, reason = self.find()
                self.assertIsNone(spots)
                self.assertIn("nonfinite", reason)

    def test_invalid_channel_shape_requires_retry(self):
        self.data[("ir105", None, self.WHEN)] = np.zeros((2, 2))
        self.assertIsNone(self.find()[0])

    def test_out_of_display_range_requires_retry(self):
        self.data[("fire-rgb", 0, self.WHEN)][self.row, self.col] = 300
        spots, reason = self.find()
        self.assertIsNone(spots)
        self.assertIn("display-index range", reason)

    def test_both_references_are_mandatory(self):
        for offsets in ([30], [30, 30], [0, 30]):
            with self.subTest(offsets=offsets):
                self.assertIsNone(self.find(refs=offsets)[0])
        del self.data[("fire-rgb", 0, self.times[2])]
        self.assertIsNone(self.find()[0])

    def test_maximum_reference_prevents_already_hot_spot_becoming_new(self):
        self.data[("fire-rgb", 0, self.times[2])][self.row, self.col] = 100
        self.assertEqual(self.find()[0], [])

    def test_prior_warm_spot_is_rejected(self):
        for when in self.times[1:]:
            self.data[("fire-rgb", 0, when)][self.row, self.col] = 70
        spots, notes = self.find()
        self.assertEqual(spots, [])
        self.assertIn("hot before (flare)", notes["rejected"])

    def test_land_and_coast_masks_are_enforced(self):
        spots, notes = self.find(land=True)
        self.assertEqual(spots, [])
        self.assertIn("land or shore", notes["rejected"])
        sea = np.ones((self.height, self.width), dtype=bool)
        sea[self.row, self.col] = False
        self.assertEqual(self.find(sea=sea)[0], [])

    def test_platform_and_known_heat_are_rejected(self):
        geo = Mock()
        geo.platform_within.return_value = True
        spots, notes = self.find(geo=geo)
        self.assertEqual(spots, [])
        self.assertIn("oil platform", notes["rejected"])
        self.assertEqual(geo.platform_within.call_args.args[-1], 3)
        memory = Memory({})
        latitude, longitude = seawatch.px_to_latlon(self.box["bbox"], self.width, self.height,
                                                  self.col, self.row)
        memory.record(latitude, longitude, "2026-11-07", 10)
        spots, notes = self.find(mem=memory)
        self.assertEqual(spots, [])
        self.assertIn("heat seen here before", notes["rejected"])

    def test_candidate_validation_and_burst_limits(self):
        current = self.data[("fire-rgb", 0, self.WHEN)]
        current[:] = 30
        for row, col in ((10, 10), (10, 25), (10, 40), (25, 10), (25, 25), (25, 40), (40, 10)):
            current[row, col] = 100
        spots, notes = self.find()
        self.assertEqual(notes["candidates"], 7)
        self.assertEqual(notes["validated"], 6)
        self.assertEqual(notes["rejected"]["validation budget"], 1)
        self.assertEqual(len(spots), 3)
        self.assertEqual(notes["rejected"]["weather (many specks at once)"], 3)

    def test_missing_clear_sea_background_requires_retry(self):
        sea = np.zeros((self.height, self.width), dtype=bool)
        sea[self.row - 4:self.row + 4, self.col - 4:self.col + 4] = True
        spots, reason = self.find(sea=sea)
        self.assertIsNone(spots)
        self.assertIn("background incomplete", reason)


if __name__ == "__main__":
    unittest.main()
