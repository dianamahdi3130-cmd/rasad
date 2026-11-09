"""Reviewed history can be corrected quietly without overwriting newer evidence."""
import copy
import datetime as dt
import itertools
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rasad import engine, repairs
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json


class ReviewedRepairTests(unittest.TestCase):
    CASE = "hormuz_20261108"
    OLD_ID = "20261108-0841_26.103_55.884"
    EVENING_ID = "20261108-1702_26.084_55.915"

    def setUp(self):
        self.now = dt.datetime(2026, 11, 9, 7, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["repairs"] = {"enabled": True, "cases": [self.CASE]}
        self.original = {
            "id": self.OLD_ID, "kind": "new", "first": "2026-11-08T08:41:00Z",
            "last": "2026-11-08T17:02:00Z", "lat": 26.1034, "lon": 55.8839,
            "best": "polar", "families": ["viirs", "modis"], "geo_slots": [],
            "srcs": ["VIIRS_NOAA20_NRT", "MODIS_NRT"],
            "polar": [{"t": t, "frp": mw, "conf": conf, "night": night} for t, mw, conf, night in (
                ("2026-11-08T08:41:00Z", 13.3, "n", False),
                ("2026-11-08T08:41:00Z", 13.3, "h", False),
                ("2026-11-08T10:21:00Z", 26.37, "n", False),
                ("2026-11-08T17:02:00Z", 47.42, "100", True),
                ("2026-11-08T17:02:00Z", 21.34, "76", True))],
            "max_frp": 47.42, "n": 5, "status": "alerted", "alerted_verdict": "مؤكد",
            "alerted_max_frp": 47.42, "alerted_at": "2026-11-08T11:23:43Z",
            "alert_updated_at": "2026-11-08T17:47:23Z", "tg_msg": 1144,
            "known_days": 0, "area_days": 1, "place": "sea", "facility": None,
            "country": "الإمارات", "where": "في البحر قرب شعم", "region_name": "مضيق هرمز",
            "regions": ["hormuz", "gulf", "oman"],
            "clip": "clips/old-v26.mp4", "snap": "clips/old-v26.jpg",
            "clip_max_frp": 26.37, "clip_peak_time": "2026-11-08T10:21:00Z", "clip_style": 18,
            "clip_updated_at": "2026-11-08T15:23:05Z", "clip_video_pending": True,
            "clip_needs_refresh": True, "media_job": "a" * 64,
            "clip_refresh_tried": "2026-11-08T21:28:01Z",
        }
        self.other = {"id": "20261108-1206_26.113_55.944", "max_frp": 71.98,
                      "srcs": ["MODIS_NRT"], "unrelated": {"preserve": True}}
        self.state = SimpleNamespace(state={}, incidents=[copy.deepcopy(self.original), copy.deepcopy(self.other)])

    def apply(self, now=None):
        return repairs.apply_reviewed(self.cfg, self.state, now or self.now)

    def by_id(self, key):
        return next(i for i in self.state.incidents if i["id"] == key)

    def test_reviewed_rebuild_preserves_thread_audit_and_unrelated_aqua(self):
        with patch("rasad.notify.tg_send", side_effect=AssertionError("No Telegram from a repair")):
            self.assertEqual(self.apply(), 1)
        self.assertEqual(len(self.state.incidents), 3)
        morning, evening = self.by_id(self.OLD_ID), self.by_id(self.EVENING_ID)
        self.assertEqual((morning["n"], morning["max_frp"], morning["last"]),
                         (6, 61.07, "2026-11-08T10:21:00Z"))
        self.assertEqual((evening["n"], evening["max_frp"]), (4, 50.59))
        self.assertEqual(morning["srcs"], ["VIIRS_NOAA20_NRT"])
        self.assertEqual(evening["srcs"], ["MODIS_NRT"])
        self.assertEqual(morning["verdict"], "مرجّح")
        self.assertEqual(evening["verdict"], "يحتاج تأكيداً")
        self.assertTrue(evening["association_uncertain"])
        self.assertEqual(evening["association"]["previous_id"], self.OLD_ID)
        self.assertEqual(morning["tg_msg"], 1144)
        for field in ("alerted_at", "alert_updated_at", "alerted_max_frp", "alerted_verdict"):
            self.assertEqual(morning[field], self.original[field])
            if field == "alerted_verdict":
                self.assertIsNone(evening[field])
            else:
                self.assertNotIn(field, evening)
        self.assertNotIn("tg_msg", evening)
        self.assertEqual(evening["status"], "new")
        for inc in (morning, evening):
            self.assertTrue(inc["recovered_historical"])
            self.assertFalse(inc["clip_video_pending"])
            self.assertNotIn("media_job", inc)
            self.assertNotIn("clip_refresh_tried", inc)
            self.assertTrue(inc["correction_note"])
        self.assertEqual(morning["clip"], self.original["clip"])
        self.assertEqual(morning["clip_max_frp"], 26.37)
        self.assertTrue(morning["clip_needs_refresh"])
        self.assertNotIn("clip", evening)
        audit = self.state.state["reviewed_repairs"][self.CASE]
        self.assertEqual(audit["original"], self.original)
        self.assertEqual(audit["status"], "applied")
        self.assertEqual(audit["applied_at"], iso(self.now))
        self.assertEqual(self.by_id(self.other["id"]), self.other)

    def test_second_run_is_idempotent_even_if_display_fields_changed_later(self):
        self.apply()
        self.by_id(self.OLD_ID)["where"] = "وصف جغرافي أحدث"
        before = copy.deepcopy(vars(self.state))
        self.assertEqual(self.apply(self.now + dt.timedelta(hours=2)), 0)
        self.assertEqual(vars(self.state), before)

    def test_complete_scan_persists_the_review_without_alerts_or_old_video_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(self.cfg)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site",
                       _geodata_dir=root / "geo", _background_media=True)
            cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 25, 57, 27]}]
            cfg["eumetview"]["fire_layers"] = []
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["clip"]["per_run"] = 0
            cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
            cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
            save_json(cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES,
                      "clip_style": engine.CLIP_STYLE, "sea_check": engine.SEA_CHECK,
                      "hello_sent": iso(self.now)})
            memory = Memory({})
            memory.record(30, 48, "2026-11-07", 10)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            save_json(cfg["_state_dir"] / "incidents.json", [self.original])
            session = Mock()
            forbidden = Mock(side_effect=AssertionError("No sources, rendering or Telegram during repair"))
            session.get = session.post = forbidden
            geo = Mock(version="offline-map")
            geo.place_kind.return_value = "sea"
            geo.facility.return_value = None
            geo.describe.return_value = ("في البحر", "الإمارات")
            with patch.multiple(engine, utcnow=Mock(return_value=self.now),
                                make_session=Mock(return_value=session), WMS=Mock(),
                                Geo=Mock(return_value=geo), Availability=Mock(),
                                latest_time=forbidden, detect_geo_layer=forbidden,
                                firms_fetch=forbidden, add_clip=forbidden, deliver=forbidden,
                                tg_send=forbidden):
                engine.run_cycle(cfg, send=True)
                result = engine.run_cycle(cfg, send=True)
            forbidden.assert_not_called()
            self.assertEqual(len(result.incidents), 2)
            self.assertEqual(result.state["reviewed_repairs"][self.CASE]["original"], self.original)
            public = load_json(cfg["_site_dir"] / "data" / "incidents.json", {})["items"]
            self.assertEqual({row["max_frp"] for row in public}, {61.07, 50.59})
            self.assertTrue(all(row["recovered_historical"] and not row["clip_video_pending"] for row in public))
            self.assertTrue(all(row["reason"].startswith(row["correction_note"]) for row in public))

    def test_disabled_or_absent_target_never_creates_historical_events(self):
        for options in ({"enabled": False, "cases": [self.CASE]}, {"enabled": True, "cases": ["unknown"]}):
            with self.subTest(options=options):
                self.cfg["repairs"] = options
                before = copy.deepcopy(vars(self.state))
                self.assertEqual(self.apply(), 0)
                self.assertEqual(vars(self.state), before)
        self.cfg["repairs"] = {"enabled": True, "cases": [self.CASE]}
        self.state.incidents = [copy.deepcopy(self.other)]
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.state.incidents, [self.other])
        self.assertEqual(self.state.state, {})

    def test_any_new_evidence_or_changed_legacy_fingerprint_is_not_overwritten(self):
        mutations = [
            lambda i: i.update(last="2026-11-08T18:00:00Z"),
            lambda i: i.update(first="2026-11-08T08:40:00Z"),
            lambda i: i.update(max_frp=100),
            lambda i: i.update(lat=26.2),
            lambda i: i.update(kind="unusual"),
            lambda i: i.update(best="geo"),
            lambda i: i["srcs"].append("mtg_frp"),
            lambda i: i["families"].append("mtg"),
            lambda i: i["geo_slots"].append("2026-11-08T16:00:00Z"),
            lambda i: i.update(observations=[{"t": "2026-11-08T17:02:00Z", "lat": 26.0841, "lon": 55.9148}]),
            lambda i: i["polar"][0].update(lat=26.1034, lon=55.8839),
            lambda i: i["polar"][0].update(frp=99),
            lambda i: i["polar"].append({"t": "2026-11-08T18:02:00Z", "frp": 47.42}),
            lambda i: i.update(polar=[]),
            lambda i: i.update(frp_series=[["2026-11-08T18:02:00Z", 47.42, "modis"]]),
            lambda i: i.update(frp_series=[["2026-11-08T17:02:00Z", 50.59, "modis"]]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed = copy.deepcopy(self.original)
                mutate(changed)
                self.state = SimpleNamespace(state={}, incidents=[changed, copy.deepcopy(self.other)])
                before = copy.deepcopy(self.state.incidents)
                self.assertEqual(self.apply(), 0)
                self.assertEqual(self.state.incidents, before)
                self.assertEqual(self.state.state["reviewed_repairs"][self.CASE]["status"], "review_needed")

    def test_duplicate_or_existing_replacement_ids_are_not_rewritten(self):
        for extra in (copy.deepcopy(self.original), {"id": self.EVENING_ID, "preserve": True}):
            with self.subTest(id=extra["id"]):
                self.state = SimpleNamespace(state={}, incidents=[copy.deepcopy(self.original), extra])
                before = copy.deepcopy(self.state.incidents)
                self.assertEqual(self.apply(), 0)
                self.assertEqual(self.state.incidents, before)

    def test_future_or_expired_evidence_is_never_installed(self):
        for when in ("2026-11-08T16:59:00Z", "2026-11-16T07:00:00Z"):
            with self.subTest(now=when):
                self.state = SimpleNamespace(state={}, incidents=[copy.deepcopy(self.original)])
                self.assertEqual(self.apply(when), 0)
                self.assertEqual(self.state.incidents, [self.original])

    def test_corrupted_bundled_observation_is_rejected_before_any_state_replacement(self):
        fixture = repairs._fixture(self.CASE)
        fixture["observations"][0]["frp"] = 999
        with patch.object(Path, "read_text", return_value=json.dumps(fixture)):
            self.assertEqual(self.apply(), 0)
        self.assertEqual(self.state.incidents, [self.original, self.other])
        self.assertEqual(self.state.state["reviewed_repairs"][self.CASE]["status"], "review_needed")

    def test_unreproducible_partition_does_not_modify_original(self):
        with patch.object(repairs.acquisitions, "groups", return_value=[]):
            self.assertEqual(self.apply(), 0)
        self.assertEqual(self.state.incidents, [self.original, self.other])

    def test_marker_saved_before_incident_file_can_resume_the_same_legacy_record(self):
        self.apply()
        marker = copy.deepcopy(self.state.state)
        self.state = SimpleNamespace(state=marker, incidents=[copy.deepcopy(self.original), copy.deepcopy(self.other)])
        self.assertEqual(self.apply(self.now + dt.timedelta(minutes=2)), 1)
        self.assertEqual(self.state.state["reviewed_repairs"][self.CASE]["original"], self.original)
        self.assertEqual(len(self.state.incidents), 3)

    def test_evening_or_invalid_old_video_is_not_assigned_to_morning(self):
        for change in ({"clip_peak_time": "2026-11-08T17:02:00Z", "clip_max_frp": 47.42},
                       {"clip_max_frp": 999}, {"clip_max_frp": None}):
            with self.subTest(change=change):
                old = copy.deepcopy(self.original)
                old.update(change)
                self.state = SimpleNamespace(state={}, incidents=[old])
                self.assertEqual(self.apply(), 1)
                morning = self.by_id(self.OLD_ID)
                self.assertNotIn("clip", morning)
                self.assertNotIn("snap", morning)
                self.assertTrue(morning["clip_needs_refresh"])
                self.assertFalse(morning["clip_video_pending"])

    def test_unsafe_old_media_path_is_not_reused(self):
        old = self.state.incidents[0]
        old.update(clip="../outside.mp4", snap="/tmp/outside.jpg")
        self.assertEqual(self.apply(), 1)
        self.assertNotIn("clip", self.by_id(self.OLD_ID))
        self.assertNotIn("snap", self.by_id(self.OLD_ID))

    def test_reviewed_partition_is_independent_of_all_within_pass_csv_orderings(self):
        rows = repairs._fixture(self.CASE)["observations"]
        passes = [[r for r in rows if r["t"] == when] for when in sorted({r["t"] for r in rows})]
        permutations = [list(itertools.permutations(batch)) for batch in passes]
        expected = [(self.OLD_ID, 6, 61.07, "مرجّح"),
                    (self.EVENING_ID, 4, 50.59, "يحتاج تأكيداً")]
        count = 0
        for ordering in itertools.product(*permutations):
            flat = [row for batch in ordering for row in batch]
            result = repairs._rebuild(flat, self.cfg, self.original, repairs._CASES[self.CASE])
            self.assertEqual([(i["id"], i["n"], i["max_frp"], i["verdict"]) for i in result], expected)
            self.assertTrue(result[1]["association_uncertain"])
            count += 1
        self.assertEqual(count, 1152)


if __name__ == "__main__":
    unittest.main()
