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

    def find(self, platform_distance, radius=None, known_heat=False, known_days=3,
             known_distance=0, when=None):
        when = when or self.WHEN
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
        dark = np.zeros((height, width), dtype=np.float32)   # the short-wave colours: no flame glow
        pics.get.side_effect = lambda layer, bbox, w, h, t, channel=None: \
            dark if channel in (1, 2) else current if t == when else previous

        # Use the real platform spatial lookup, with one platform at a known distance.
        platform_lat = lat + math.degrees(platform_distance / 6371.0)
        geo = Geo.__new__(Geo)
        geo.fac = GeoIndex([[platform_lat, lon, "Test platform", "منصة نفطية", "platform"]])
        self.assertAlmostEqual(haversine_km(lat, lon, platform_lat, lon), platform_distance, places=6)
        memory = Memory({})
        if known_heat:
            for offset in range(1, known_days + 1):
                day = (when - dt.timedelta(days=offset)).date().isoformat()
                memory.record(lat + known_distance / 111.32, lon, day, 10.0)
        sea_mask = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
        with patch.dict(seawatch._SEA, {}, clear=True), \
                patch.object(seawatch, "land_mask_points", return_value=sea_mask), \
                patch.object(seawatch, "sun_elevation", return_value=-30.0), \
                patch.object(seawatch, "_ir105_change", return_value=0.7):
            return seawatch.find_spots(cfg, pics, box, when, mem=memory, geo=geo)

    def test_independent_spot_nine_km_from_platform_is_kept(self):
        spots, notes = self.find(8.9)
        self.assertEqual(len(spots), 1)
        self.assertEqual(notes["rejected"], {})
        self.assertGreater(spots[0]["above_sea"], 3.0)

    def test_heat_two_km_from_platform_stays_excluded(self):
        spots, notes = self.find(2.0)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"oil platform": 1})

    def test_persistent_local_heat_stays_excluded_with_smaller_platform_radius(self):
        spots, notes = self.find(8.9, known_heat=True)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"heat seen here before": 1})

    def test_one_old_heat_point_does_not_hide_a_new_night_spot(self):
        for distance in (0, 8):
            with self.subTest(distance=distance):
                spots, notes = self.find(8.9, known_heat=True, known_days=1, known_distance=distance)
                self.assertEqual(len(spots), 1, notes)
                self.assertNotIn("heat seen here before", notes["rejected"])

    def test_a_persistent_source_eight_km_away_does_not_hide_a_new_night_spot(self):
        spots, notes = self.find(8.9, known_heat=True, known_distance=8)
        self.assertEqual(len(spots), 1, notes)
        self.assertEqual(notes["rejected"], {})

    def test_current_night_across_utc_midnight_cannot_be_a_third_old_heat_day(self):
        when = self.WHEN + dt.timedelta(hours=3)
        self.assertEqual(when.day, 8)
        # Dates7,6,5 include the current night (Nov7), leaving only two prior dates.
        spots, notes = self.find(8.9, known_heat=True, known_days=3, when=when)
        self.assertEqual(len(spots), 1, notes)

    def test_explicit_ten_km_radius_is_respected(self):
        spots, notes = self.find(8.9, radius=10.0)
        self.assertEqual(spots, [])
        self.assertEqual(notes["rejected"], {"oil platform": 1})


class PlatformRadiusCacheTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 7, 23, tzinfo=dt.timezone.utc)

    def watch(self, old_radius, old_history="current"):
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
                 "sea_sparkle": copy.deepcopy(history), "slots": general_slots,
                 "sea_sparkle_min_days": 3}
        if old_history == "current":
            state["sea_history_rules"] = seawatch.history_rules(cfg["seawatch"])
        elif old_history is not None:
            state["sea_history_rules"] = old_history
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

    def test_changed_history_rules_recheck_completed_slots_and_preserve_evidence(self):
        current = seawatch.history_rules(engine.DEFAULTS["seawatch"])
        for field, prior in (("known_heat_km", 20), ("known_heat_exclusion_km", 10),
                             ("known_heat_min_days", 1), ("version", 0)):
            with self.subTest(field=field):
                changed = dict(current, **{field: prior})
                state, _, latest_key, find = self.watch(3.0, old_history=changed)
                find.assert_called_once()
                self.assertEqual(set(state["seawatch"]), {latest_key})
                self.assertEqual(state["sea_history_rules"], current)

    def test_first_history_rule_migration_rechecks_completed_slots(self):
        state, _, latest_key, find = self.watch(3.0, old_history=None)
        find.assert_called_once()
        self.assertEqual(set(state["seawatch"]), {latest_key})
        self.assertEqual(state["sea_history_rules"], seawatch.history_rules(engine.DEFAULTS["seawatch"]))

    def test_unchanged_history_rules_preserve_completed_slots(self):
        state, old_done, _, find = self.watch(3.0)
        find.assert_not_called()
        self.assertEqual(state["seawatch"], old_done)


class SparkleHistoryTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 7, 23, tzinfo=dt.timezone.utc)

    FIELD = {"flare_km": 2.0, "spot_days": 5}     # a field of platforms NASA's long-term map knows

    def watch(self, history, now=None, old_threshold=3, threshold=3, completed=False, platforms=True):
        now = now or self.NOW
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update({"boxes": [{"id": "test", "bbox": [55, 25, 57, 27]}],
                                "slots_per_run": 3, "catchup_hours": 0,
                                "delay_min": 0, "sparkle_min_days": threshold})
        latest = floor_time(now, 10)
        done = {"test|" + iso(latest - dt.timedelta(minutes=10 * i)):
                iso(latest - dt.timedelta(minutes=10 * i)) for i in range(3)} if completed else {}
        state = {"seawatch": done.copy(), "sea_sparkle": copy.deepcopy(history),
                 "sea_platform_km": cfg["seawatch"]["platform_km"],
                 "sea_history_rules": seawatch.history_rules(cfg["seawatch"])}
        if old_threshold is not None:
            state["sea_sparkle_min_days"] = old_threshold
        availability = Mock()
        availability.has.return_value = True

        def spots(cfg, pics, box, t, mem, geo, refs):
            # For the midnight case, keep two pictures 20 minutes apart.
            if now.hour == 0 and t.minute == 0:
                return [], {}
            return [{"t": iso(t), "lat": 26.174, "lon": 56.0761, "rise": 12.0}], {}

        with patch.object(seawatch, "Pictures"), \
                patch.object(seawatch, "_night", return_value=[True]), \
                patch("rasad.patterns.platform_area", return_value=self.FIELD if platforms else None), \
                patch.object(seawatch, "find_spots", side_effect=spots) as find:
            found = seawatch.watch(cfg, Mock(), state, now, avail=availability)
        return found, state, find, done

    def test_one_old_neighbor_night_does_not_hide_new_fire(self):
        found, state, _, _ = self.watch({"522,1120": ["2026-11-04"]})
        self.assertEqual(len(found), 3)
        self.assertEqual(state["sea_sparkle"]["522,1120"], ["2026-11-04"])
        self.assertEqual(state["sea_sparkle"]["523,1121"], ["2026-11-07"])

    def test_three_independent_prior_nights_suppress_recurrent_specks(self):
        found, _, _, _ = self.watch({"522,1120": ["2026-11-03", "2026-11-04", "2026-11-05"]})
        self.assertEqual(found, [])

    def test_in_open_water_a_few_nights_of_ship_fires_are_not_a_flare(self):
        # Hormuz, 24 Nov 2026: a ship fire the night before and specks on three other nights held a
        # burning ship's pairs for 1.5 hours; in open water a flare is a speck on most nights
        nights = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
        found, _, _, _ = self.watch({"522,1120": nights}, platforms=False)
        self.assertEqual(len(found), 3)
        found, _, _, _ = self.watch({"522,1120": nights + ["2026-10-31", "2026-11-01", "2026-11-02"]},
                                    platforms=False)
        self.assertEqual(found, [], "seven of the fourteen nights kept: a flare the maps do not know")
        self.assertEqual(seawatch.sparkle_nights(26.39, 56.25, 3), 7)
        self.assertEqual(seawatch.sparkle_nights(24.85, 53.70, 3), 3, "Zakum's field: the plain rule")

    def test_same_historical_night_in_multiple_cells_counts_once(self):
        found, _, _, _ = self.watch({"522,1120": ["2026-11-04"],
                                      "522,1121": ["2026-11-04"],
                                      "523,1120": ["2026-11-04"]})
        self.assertEqual(len(found), 3)

    def test_pair_across_midnight_shares_one_night(self):
        now = dt.datetime(2026, 11, 8, 0, 10, tzinfo=dt.timezone.utc)
        # Two older nights must not become three when the newer picture crosses midnight.
        found, state, _, _ = self.watch({"522,1120": ["2026-11-03", "2026-11-04"]}, now=now)
        self.assertEqual({row["t"] for row in found},
                         {"2026-11-07T23:50:00Z", "2026-11-08T00:10:00Z"})
        self.assertEqual(state["sea_sparkle"]["523,1121"], ["2026-11-07"])

    def test_changed_threshold_rechecks_completed_slots(self):
        found, state, find, _ = self.watch({}, old_threshold=1, completed=True)
        self.assertEqual(find.call_count, 3)
        self.assertEqual(len(found), 3)
        self.assertEqual(state["sea_sparkle_min_days"], 3)

    def test_missing_threshold_marker_rechecks_completed_slots(self):
        found, state, find, _ = self.watch({}, old_threshold=None, completed=True)
        self.assertEqual(find.call_count, 3)
        self.assertEqual(len(found), 3)
        self.assertEqual(state["sea_sparkle_min_days"], 3)

    def test_unchanged_markers_preserve_completed_slots(self):
        found, state, find, done = self.watch({}, completed=True)
        find.assert_not_called()
        self.assertEqual(found, [])
        self.assertEqual(state["seawatch"], done)

    def test_configured_night_threshold_is_respected(self):
        found, state, _, _ = self.watch({"522,1120": ["2026-11-04"]}, threshold=1)
        self.assertEqual(found, [])
        self.assertEqual(state["sea_sparkle_min_days"], 1)


class DayReplay(unittest.TestCase):
    def test_the_day_finder_can_be_replayed_at_any_picture_detail(self):
        # for measuring it on real daytime pictures before its detail is changed (seascan --day --km)
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"]["boxes"] = [{"id": "first", "bbox": [54, 25, 54.2, 25.2]}, {"id": "second", "bbox": [55, 25, 55.2, 25.2]}]
        asked = []

        def finder(c, pics, box, t, mem=None, geo=None, refs=None):
            asked.append((box["id"], iso(t), c["seawatch"]["daytime"]["km_per_px"], tuple(refs)))
            return [{"src": "mtg_day_spot", "t": iso(t), "lat": 25.1, "lon": 54.1, "rise": 20.0, "area_px": 5}], {"candidates": 1}
        start = dt.datetime(2026, 11, 10, 7, 30, tzinfo=dt.timezone.utc)
        with patch("rasad.daywatch.find_spots", side_effect=finder), patch.object(seawatch.Pictures, "prefetch") as prefetch:
            rows = seawatch.scan_day(cfg, Mock(), start, start + dt.timedelta(minutes=10), boxes=["first"], km=0.25)
        self.assertEqual(asked, [("first", "2026-11-10T07:30:00Z", 0.25, (30, 60)), ("first", "2026-11-10T07:40:00Z", 0.25, (30, 60))])
        self.assertEqual([(r["box"], r["t"]) for r in rows], [("first", "2026-11-10T07:30:00Z"), ("first", "2026-11-10T07:40:00Z")])
        requests = prefetch.call_args_list[0].args[0]
        self.assertEqual(len(requests), 6, "the picture and its check layer, now and at the two references")
        self.assertEqual(tuple(requests[0][2:4]), seawatch.box_size(cfg["seawatch"]["boxes"][0], 0.25))
        self.assertEqual(cfg["seawatch"]["daytime"]["km_per_px"], 0.5, "the live setting is not touched")
        self.assertEqual(len(seawatch.group(rows)), 1)


if __name__ == "__main__":
    unittest.main()
