"""An accepted one-frame night spot can be reviewed without becoming an incident."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rasad import engine, preliminary
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json


class PreliminaryCandidateTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 13, 19, 30, tzinfo=UTC)
    WHEN = dt.datetime(2026, 11, 13, 18, 50, tzinfo=UTC)

    def setUp(self):
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["regions"] = [
            {"id": "oman", "name": "عُمان", "bbox": [51.9, 16.6, 60, 26.5]},
            {"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.4, 25.3, 57.6, 27.3]}]

    def candidate(self, **changes):
        result = {"src": "mtg_spot", "t": iso(self.WHEN), "lat": 26.3716,
                  "lon": 56.2988, "rise": 53.3}
        result.update(changes)
        return result

    def preview(self, candidates=None, *, incidents=None, state=None, now=None):
        state = state if state is not None else {"sea_candidates": candidates or [self.candidate()]}
        return preliminary.public_candidates(self.cfg, state, incidents or [], now or self.NOW)

    def test_single_accepted_spot_is_explicitly_uncertain_and_has_no_megawatts(self):
        items = self.preview()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["verdict"], "يحتاج تأكيداً")
        self.assertTrue(item["preliminary"])
        self.assertNotIn("حريق", item["headline"])
        self.assertIn("أولية", item["headline"])
        self.assertIn("مضيق هرمز", item["headline"])
        self.assertEqual(item["first"], "2026-11-13T18:50:00Z")
        self.assertIn("21:50", item["start_text"])
        self.assertIsNone(item["max_frp"])
        self.assertIsNone(item["frp_series"])
        self.assertIsNone(item["clip"])
        self.assertIn("أولي", item["clip_note"])
        self.assertNotIn("53.3", str(item))

    def test_views_do_not_mutate_state_incidents_or_configuration(self):
        state = {"sea_candidates": [self.candidate()], "seen": {"keep": True},
                 "sea_sparkle": {"ignored": ["2026-11-01"]}}
        incidents = [{"id": "unrelated", "observations": []}]
        before = copy.deepcopy((self.cfg, state, incidents))
        items = preliminary.public_candidates(self.cfg, state, incidents, self.NOW)
        items[0]["regions"].append("changed-preview")
        self.assertEqual((self.cfg, state, incidents), before)

    def test_only_night_source_is_eligible(self):
        cases = [self.candidate(src="mtg_day_spot"), self.candidate(src="mtg_frp"),
                 {k: v for k, v in self.candidate().items() if k != "src"}]
        self.assertEqual(self.preview(cases), [])
        self.cfg["seawatch"]["catchup_hours"] = 24
        self.assertEqual(self.preview([self.candidate(t="2026-11-13T09:00:00Z")]), [])

    def test_malformed_outside_future_and_stale_spots_do_not_escape(self):
        cases = [None, "invalid", self.candidate(lat=True), self.candidate(lon=float("nan")),
                 self.candidate(lat=91), self.candidate(lat=31, lon=30),
                 self.candidate(rise=float("inf")), self.candidate(rise=True),
                 self.candidate(rise=0), self.candidate(t="not a date"),
                 self.candidate(t=iso(self.NOW + dt.timedelta(seconds=1))),
                 self.candidate(t=iso(self.NOW - dt.timedelta(hours=4, seconds=1)))]
        self.assertEqual(self.preview(cases), [])
        self.assertEqual(len(self.preview([self.candidate(t=iso(self.NOW - dt.timedelta(hours=4)))])), 1)

    def test_disabled_finder_does_not_publish_its_pending_spots(self):
        self.cfg["seawatch"]["enabled"] = False
        self.assertEqual(self.preview(), [])

    def test_prior_nights_filter_does_not_count_current_night_or_duplicate_days(self):
        key = f"{int(26.3716 // .05)},{int(56.2988 // .05)}"
        state = {"sea_candidates": [self.candidate()],
                 "sea_sparkle": {key: ["2026-11-11", "2026-11-12", "2026-11-13", "2026-11-12"]}}
        self.assertEqual(len(self.preview(state=state)), 1)
        state["sea_sparkle"][key].append("2026-11-10")
        self.assertEqual(self.preview(state=state), [])

    def test_matching_observation_removes_preview_but_nearby_other_event_does_not(self):
        observation = {"src": "mtg_spot", "t": iso(self.WHEN),
                       "lat": 26.3717, "lon": 56.2989}
        self.assertEqual(self.preview(incidents=[{"observations": [observation]}]), [])
        for changes in ({"src": "mtg_frp"}, {"t": iso(self.WHEN - dt.timedelta(minutes=10))},
                        {"lon": 56.35}):
            with self.subTest(changes=changes):
                row = dict(observation, **changes)
                self.assertEqual(len(self.preview(incidents=[{"observations": [row]}])), 1)

    def test_legacy_slot_needs_exclusive_night_source_and_matching_position(self):
        old = {"srcs": ["mtg_spot"], "geo_slots": [iso(self.WHEN)],
               "lat": 26.3716, "lon": 56.2988}
        self.assertEqual(self.preview(incidents=[old]), [])
        for changes in ({"srcs": ["mtg_spot", "viirs"]}, {"srcs": []}, {"lon": 56.35},
                        {"geo_slots": [iso(self.WHEN - dt.timedelta(minutes=10))]}):
            self.assertEqual(len(self.preview(incidents=[dict(old, **changes)])), 1)

    def test_exact_duplicates_are_removed_without_merging_distinct_nearby_spots(self):
        rows = [self.candidate(), self.candidate(), self.candidate(lat=26.38)]
        items = self.preview(rows)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item["id"] for item in items}), 2)

    def test_newest_preview_cap_is_deterministic_and_does_not_change_state(self):
        rows = [self.candidate(t=iso(self.WHEN - dt.timedelta(minutes=i)), lat=26.3716 + .0001 * i)
                for i in range(40)]
        items = self.preview(rows)
        self.assertEqual(len(items), 25)
        self.assertEqual(items[0]["last"], iso(self.WHEN))
        self.assertEqual(items[-1]["last"], iso(self.WHEN - dt.timedelta(minutes=24)))
        self.assertEqual(items, self.preview(list(reversed(rows))))

    def test_publish_preview_is_replaced_by_real_observation_without_incident_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.cfg["_site_dir"] = Path(folder)
            candidate = self.candidate()
            state = SimpleNamespace(state={"sea_candidates": [candidate]}, incidents=[])
            before = copy.deepcopy(state.state)
            engine.publish(self.cfg, state, {}, self.NOW, False, 0, finished=self.NOW)
            published = load_json(Path(folder) / "data/incidents.json", {})["items"]
            self.assertEqual(len(published), 1)
            self.assertTrue(published[0]["preliminary"])
            self.assertEqual(state.incidents, [])
            self.assertEqual(state.state, before)
            real = {"id": "real-event", "headline": "حريق في البحر", "last": candidate["t"],
                    "priority": "high", "observations": [dict(candidate)]}
            state.incidents.append(real)
            engine.publish(self.cfg, state, {}, self.NOW, False, 1, finished=self.NOW)
            published = load_json(Path(folder) / "data/incidents.json", {})["items"]
            self.assertEqual([item["id"] for item in published], ["real-event"])
            self.assertNotIn("preliminary", published[0])

    def test_whole_cycle_preview_cannot_train_memory_or_alert_at_lowest_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(self.cfg)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
            cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
            cfg["eumetview"]["fire_layers"] = []
            cfg["firms"]["map_key"] = "offline-test-key"
            cfg["clip"]["enabled"] = False
            cfg["seawatch"].update(look_back=False, alerts=True)
            cfg["seawatch"]["early_alerts"]["enabled"] = False
            for feature in ("satpic", "watch", "smoke", "flares", "recovery"):
                cfg[feature]["enabled"] = False
            cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
            save_json(cfg["_state_dir"] / "state.json", {
                "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                "sea_check": engine.SEA_CHECK, "hello_sent": iso(self.NOW),
                "bootstrapped": iso(self.NOW), "sea_candidates": [self.candidate()]})
            memory = Memory({})
            memory.record(30, 48, "2026-11-12", 10)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            saved_memory = copy.deepcopy(memory.cells)
            session = Mock()
            for method in ("get", "post", "put"):
                getattr(session, method).side_effect = AssertionError("No network in offline cycle")
            geo = Mock(version="offline-map")
            deliver = Mock(side_effect=AssertionError("Preview must not alert"))
            with patch.multiple(engine, utcnow=Mock(return_value=self.NOW),
                                make_session=Mock(return_value=session), WMS=Mock(), Geo=Mock(return_value=geo),
                                firms_fetch=Mock(return_value=[]), add_clip=Mock(), deliver=deliver,
                                tg_send=Mock(side_effect=AssertionError("Unexpected Telegram request"))), \
                    patch.object(engine.seawatch, "watch", return_value=[]):
                result = engine.run_cycle(cfg, send=True)
            self.assertEqual(result.incidents, [])
            self.assertEqual(result.memory, saved_memory)
            deliver.assert_not_called()
            session.get.assert_not_called()
            session.post.assert_not_called()
            items = load_json(cfg["_site_dir"] / "data/incidents.json", {})["items"]
            self.assertEqual(len(items), 1)
            self.assertTrue(items[0]["preliminary"])
            self.assertEqual(items[0]["verdict"], "يحتاج تأكيداً")


if __name__ == "__main__":
    unittest.main()
