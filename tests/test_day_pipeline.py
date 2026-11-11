"""Offline regressions for scheduling and isolating the experimental daytime finder."""
import copy
import datetime as dt
import sys
import types
import unittest
from unittest.mock import Mock, patch

from rasad import engine, seawatch
from rasad.util import iso


class DayWatchPipelineTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 8, 9, tzinfo=dt.timezone.utc)
    DAY = "mtg_day_spot"

    def config(self, slots=1):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update({
            "boxes": [{"id": "test", "bbox": [55, 25, 56, 26]}],
            "slots_per_run": slots, "catchup_hours": 0, "delay_min": 0,
            "daytime": {"enabled": True, "ref_minutes": [30, 60], "min_sun_elev": 6},
        })
        return cfg

    def state(self, cfg):
        return {"sea_platform_km": cfg["seawatch"]["platform_km"],
                "sea_sparkle_min_days": cfg["seawatch"].get("sparkle_min_days", 3)}

    @staticmethod
    def spot(t, src=None):
        row = {"t": iso(t), "lat": 25.5, "lon": 55.5, "rise": 18.0}
        if src is not None:
            row["src"] = src
        return row

    def run_watch(self, cfg, state, now=None, day=None, night=None, availability=None,
                  sun=25.0, at_night=False):
        day = day if day is not None else Mock(side_effect=lambda c, p, b, t, *a, **kw:
                                              ([self.spot(t, self.DAY)], {}))
        night = night if night is not None else Mock(return_value=([], {}))
        availability = availability if availability is not None else Mock()
        if availability.has.return_value is not False and availability.has.side_effect is None:
            availability.has.return_value = True
        pictures = Mock(cache={})
        module = types.ModuleType("rasad.daywatch")
        module.find_spots = day
        with patch.dict(sys.modules, {"rasad.daywatch": module}), \
                patch.object(seawatch, "Pictures", return_value=pictures), \
                patch.object(seawatch, "find_spots", night), \
                patch.object(seawatch, "_night", return_value=[at_night]), \
                patch.object(seawatch, "sun_elevation", return_value=sun):
            found = seawatch.watch(cfg, Mock(), state, now or self.NOW, avail=availability)
        return found, day, night, pictures

    def test_two_times_persist_across_runs_without_recording_night_history(self):
        cfg = self.config()
        state = self.state(cfg)
        history = {"510,1110": ["2026-11-04", "2026-11-05", "2026-11-06"]}
        state["sea_sparkle"] = copy.deepcopy(history)
        found, _, _, pictures = self.run_watch(cfg, state)
        self.assertEqual(found, [])
        self.assertEqual(state["sea_candidates"][0]["src"], self.DAY)
        self.assertEqual(state["sea_sparkle"], history)
        requests = pictures.prefetch.call_args.args[0]
        self.assertEqual(len(requests), 6)
        self.assertEqual([r[-1] for r in requests if len(r) == 6], [0, 0, 0])
        self.assertEqual(sum(r[0] == cfg["seawatch"]["check_layer"] for r in requests), 3)

        found, _, _, _ = self.run_watch(cfg, state, now=self.NOW + dt.timedelta(minutes=10))
        self.assertEqual({row["t"] for row in found},
                         {iso(self.NOW), iso(self.NOW + dt.timedelta(minutes=10))})
        self.assertTrue(all(row["src"] == self.DAY and row["family"] == "mtg"
                            and row["experimental"] and row["daytime"]
                            and row["frp"] is None for row in found))
        self.assertEqual(state["sea_sparkle"], history)
        self.assertEqual(state["sea_day_status"]["checked"], 1)
        self.assertEqual(state["sea_day_status"]["candidates"], 1)
        self.assertEqual(state["sea_day_status"]["spots"], 2)
        self.assertEqual(state["sea_day_status"]["last_checked"],
                         iso(self.NOW + dt.timedelta(minutes=10)))

    def test_status_says_whether_a_daylit_picture_is_waiting(self):
        cfg = self.config()
        state = self.state(cfg)
        _, day, _, _ = self.run_watch(cfg, state, sun=-20.0, at_night=True)
        day.assert_not_called()
        self.assertIs(state["sea_day_status"]["daylight"], False)
        self.run_watch(cfg, state, sun=25.0)
        self.assertIs(state["sea_day_status"]["daylight"], True)

    def test_day_and_night_times_cannot_confirm_each_other(self):
        cfg = self.config(slots=2)
        state = self.state(cfg)
        day = Mock(side_effect=lambda c, p, b, t, *a, **kw:
                   ([self.spot(t, self.DAY)] if t < self.NOW else [], {}))
        night = Mock(side_effect=lambda c, p, b, t, *a, **kw:
                     ([self.spot(t)] if t == self.NOW else [], {}))
        found, _, _, _ = self.run_watch(cfg, state, day=day, night=night, at_night=True)
        self.assertEqual(found, [])
        self.assertEqual({row["src"] for row in state["sea_candidates"]},
                         {seawatch.SRC, self.DAY})

    def test_missing_ir_download_remains_pending_and_retries(self):
        cfg = self.config()
        state = self.state(cfg)
        day = Mock(return_value=(None, "IR105 missing"))
        found, _, _, _ = self.run_watch(cfg, state, day=day)
        self.assertEqual(found, [])
        self.assertNotIn("day:test|" + iso(self.NOW), state["seawatch"])
        self.assertEqual(state["sea_day_status"]["missing"], 1)
        self.assertEqual(state["sea_day_status"]["checked"], 0)
        day.return_value = ([], {})
        self.run_watch(cfg, state, day=day)
        self.assertEqual(day.call_count, 2)
        self.assertIn("day:test|" + iso(self.NOW), state["seawatch"])

    def test_both_day_references_and_ir_availability_are_mandatory(self):
        for missing_layer, minutes in (("layer", 60), ("check_layer", 0)):
            with self.subTest(layer=missing_layer, minutes=minutes):
                cfg = self.config()
                state = self.state(cfg)
                absent = self.NOW - dt.timedelta(minutes=minutes)
                availability = Mock()
                availability.has.side_effect = lambda layer, t, box: not (
                    layer == cfg["seawatch"][missing_layer] and t == absent)
                _, day, _, pictures = self.run_watch(cfg, state, availability=availability)
                day.assert_not_called()
                pictures.prefetch.assert_not_called()
                self.assertEqual(state["seawatch"], {})
                self.assertEqual(state["sea_day_status"]["missing"], 1)

    def test_changing_day_settings_clears_only_day_state(self):
        cfg = self.config()
        state = self.state(cfg)
        self.run_watch(cfg, state, day=Mock(return_value=([], {})))
        night_key = "test|" + iso(self.NOW)
        day_key = "day:" + night_key
        state["seawatch"][night_key] = iso(self.NOW)
        night_candidate = self.spot(self.NOW - dt.timedelta(minutes=10))
        state["sea_candidates"] = [night_candidate.copy(),
                                   self.spot(self.NOW - dt.timedelta(minutes=20), self.DAY)]
        history = {"510,1110": ["2026-11-06"]}
        state["sea_sparkle"] = copy.deepcopy(history)
        unrelated = {"keep": True}
        state["slots"] = unrelated
        cfg["seawatch"]["daytime"]["min_rise"] = 22
        _, day, night, _ = self.run_watch(cfg, state, day=Mock(return_value=(None, "missing")))
        day.assert_called_once()
        night.assert_not_called()
        self.assertEqual(state["seawatch"], {night_key: iso(self.NOW)})
        self.assertNotIn(day_key, state["seawatch"])
        self.assertEqual(state["sea_candidates"], [night_candidate])
        self.assertEqual(state["sea_sparkle"], history)
        self.assertIs(state["slots"], unrelated)

    def test_legacy_source_less_candidates_belong_only_to_night(self):
        cfg = self.config()
        legacy = self.spot(self.NOW - dt.timedelta(minutes=10))
        state = self.state(cfg)
        state["sea_candidates"] = [legacy.copy()]
        found, _, _, _ = self.run_watch(cfg, state)
        self.assertEqual(found, [])
        self.assertEqual(state["sea_candidates"][0], legacy)

        cfg["seawatch"]["daytime"]["enabled"] = False
        night = Mock(return_value=([self.spot(self.NOW)], {}))
        state = self.state(cfg)
        state["sea_candidates"] = [legacy.copy()]
        found, _, _, _ = self.run_watch(cfg, state, night=night, at_night=True, sun=-20)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(row["src"] == seawatch.SRC for row in found))

    def test_daytime_is_skipped_when_disabled_or_unconfigured(self):
        for daytime in ({"enabled": False}, None):
            with self.subTest(daytime=daytime):
                cfg = self.config()
                if daytime is None:
                    del cfg["seawatch"]["daytime"]
                else:
                    cfg["seawatch"]["daytime"] = daytime
                state = self.state(cfg)
                _, day, _, pictures = self.run_watch(cfg, state)
                day.assert_not_called()
                pictures.prefetch.assert_not_called()
                self.assertFalse(state["sea_day_status"]["enabled"])

    def test_shared_slot_limit_bounds_prefetch_and_preserves_both_sources(self):
        cfg = self.config(slots=2)
        cfg["seawatch"]["catchup_hours"] = 3
        state = self.state(cfg)
        night = Mock(side_effect=lambda c, p, b, t, *a, **kw: ([self.spot(t)], {}))
        found, day, night, pictures = self.run_watch(cfg, state, night=night, at_night=True)
        self.assertEqual(day.call_count, 2)
        self.assertEqual(night.call_count, 2)
        self.assertEqual(pictures.prefetch.call_count, 2)
        requests = [request for call in pictures.prefetch.call_args_list for request in call.args[0]]
        self.assertEqual(len(requests), 16)  # two times, 2 night and 6 day images per time
        self.assertEqual(len(found), 4)
        self.assertEqual({row["src"] for row in found}, {seawatch.SRC, self.DAY})
        self.assertEqual(state["sea_day_status"]["waiting"], 16)
        self.assertEqual(pictures.cache, {})

    def test_elapsed_budget_defers_all_day_jobs(self):
        cfg = self.config(slots=2)
        cfg["seawatch"]["budget_s"] = -1
        state = self.state(cfg)
        found, day, _, pictures = self.run_watch(cfg, state)
        self.assertEqual(found, [])
        day.assert_not_called()
        pictures.prefetch.assert_not_called()
        self.assertEqual(state["sea_day_status"]["waiting"], 2)
        self.assertEqual(state["seawatch"], {})


if __name__ == "__main__":
    unittest.main()
