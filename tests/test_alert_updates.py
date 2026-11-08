"""Offline full-cycle regressions for intensity posts, retries, and refreshed media."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory
from rasad.updates import peak_reading
from rasad.util import UTC, iso, load_json, save_json


class AlertUpdateIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        self.cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 25, 57, 27]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["telegram"].update(bot_token="offline-test-token", chat_id="offline-test-chat")
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": "2026-11-08T10:00:00Z"})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.old_clip = "clips/fire-v51.mp4"
        self.old_snap = "clips/fire-v51.png"
        for name in (self.old_clip, self.old_snap):
            path = self.cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"previous clip")
        self.save_incident({
            "id": "fire", "kind": "new", "first": "2026-11-08T10:00:00Z",
            "last": "2026-11-08T11:50:00Z", "lat": 26.0, "lon": 56.0, "best": "geo",
            "families": ["mtg", "viirs"], "srcs": ["mtg_frp", "VIIRS_NOAA20_NRT"],
            "geo_slots": ["2026-11-08T11:00:00Z", "2026-11-08T11:10:00Z"], "polar": [],
            "max_frp": 164, "n": 3, "status": "alerted", "verdict": "مؤكد",
            "alerted_verdict": "مؤكد", "alerted_max_frp": 51,
            "alerted_at": "2026-11-08T11:00:00Z", "alert_updated_at": "2026-11-08T11:00:00Z",
            "tg_msg": 123, "known_days": 0, "area_days": 0,
            "frp_series": [["2026-11-08T10:10:00Z", 51, "mtg"],
                           ["2026-11-08T11:50:00Z", 164, "mtg"]],
            "clip": self.old_clip, "snap": self.old_snap, "clip_max_frp": 51,
            "place": "sea", "facility": None, "country": "عُمان", "priority": "high",
            "_pos": [26.0, 56.0, "offline-map"]})
        self.refresh_ok = True
        self.render = Mock(side_effect=self.render_clip)
        self.deliver = Mock(return_value=456)
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network in these tests")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر قرب مضيق هرمز", "عُمان")
        self.source = Mock(side_effect=AssertionError("No source requests in these tests"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now), make_session=Mock(return_value=self.session),
            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(), latest_time=self.source,
            detect_geo_layer=self.source, firms_fetch=self.source, add_clip=self.render,
            deliver=self.deliver, tg_send=Mock(side_effect=AssertionError("No direct Telegram requests")))
        patches.start()
        self.addCleanup(patches.stop)

    def incident(self):
        return load_json(self.cfg["_state_dir"] / "incidents.json", [])[0]

    def save_incident(self, incident):
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])

    def change_incident(self, **changes):
        incident = self.incident()
        incident.update(changes)
        self.save_incident(incident)

    def render_clip(self, cfg, wms, state, incident, now, refresh=False):
        if not self.refresh_ok:
            return False
        peak = peak_reading(incident)
        for field, suffix in (("clip", "mp4"), ("snap", "png")):
            name = f"clips/{incident['id']}-v{peak[1]:g}.{suffix}"
            path = cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"refreshed media")
            incident[field] = name
        incident["clip_max_frp"] = peak[1]
        incident["clip_peak_time"] = peak[0]
        incident["clip_style"] = engine.CLIP_STYLE
        incident["clip_updated_at"] = iso(now)
        incident.pop("clip_needs_refresh", None)
        return True

    def cycle(self):
        state = engine.run_cycle(self.cfg, send=True)
        self.assertEqual(len(state.incidents), 1)
        self.source.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
        return state.incidents[0]

    def test_same_verdict_surge_posts_one_threaded_refreshed_video(self):
        incident = self.cycle()
        self.deliver.assert_called_once()
        post = self.deliver.call_args
        self.assertIn("ارتفاع شدة الحريق", post.args[2])
        self.assertNotIn("مؤكد الآن", post.args[2])
        self.assertEqual(post.args[3], self.cfg["_site_dir"] / "clips/fire-v164.mp4")
        self.assertTrue(post.args[3].exists())
        self.assertTrue(post.args[4])
        self.assertEqual(post.kwargs["reply_to"], 123)
        self.assertTrue(self.render.call_args.kwargs["refresh"])
        self.assertEqual(incident["alerted_max_frp"], 164)
        self.assertEqual(incident["alert_updated_at"], iso(self.now))
        self.assertEqual(incident["tg_msg"], 123)
        self.assertFalse(incident["clip_video_pending"])

    def test_identical_cycle_and_small_181_mw_fluctuation_do_not_repeat(self):
        self.cycle()
        self.cycle()
        incident = self.incident()
        incident["max_frp"] = 181
        incident["frp_series"].append(["2026-11-08T12:01:00Z", 181, "mtg"])
        incident["last"] = "2026-11-08T12:01:00Z"
        self.save_incident(incident)
        self.now += dt.timedelta(minutes=45)
        self.cycle()
        self.deliver.assert_called_once()
        self.assertEqual(self.render.call_count, 2, "the website clip follows new readings independently of posts")
        self.assertEqual(self.incident()["clip_max_frp"], 181)
        self.assertEqual(self.incident()["alerted_max_frp"], 164)

    def test_failed_delivery_keeps_baseline_and_retries_without_new_detection(self):
        self.deliver.return_value = None
        incident = self.cycle()
        self.assertEqual(incident["alerted_max_frp"], 51)
        self.assertEqual(incident["alert_updated_at"], "2026-11-08T11:00:00Z")
        self.assertEqual(incident["status"], "alerted")
        self.deliver.return_value = 456
        self.now += dt.timedelta(minutes=3)
        incident = self.cycle()
        self.assertEqual(self.deliver.call_count, 2)
        self.assertEqual(incident["alerted_max_frp"], 164)
        self.assertEqual(incident["alert_updated_at"], iso(self.now))

    def test_cooldown_expiry_rechecks_untouched_incident(self):
        self.change_incident(alert_updated_at="2026-11-08T11:45:00Z")
        self.cycle()
        self.deliver.assert_not_called()
        self.render.assert_called_once()  # Website media can refresh during the notification cooldown.
        self.now += dt.timedelta(minutes=15)
        self.cycle()
        self.deliver.assert_called_once()

    def test_simultaneous_verdict_and_strength_changes_send_one_combined_post(self):
        self.change_incident(alerted_verdict="مرجّح")
        incident = self.cycle()
        self.deliver.assert_called_once()
        self.assertIn("الحريق مؤكد الآن وارتفعت شدته", self.deliver.call_args.args[2])
        self.assertEqual(incident["alerted_verdict"], "مؤكد")
        self.assertEqual(incident["alerted_max_frp"], 164)

    def test_old_peak_with_fresh_incident_last_does_not_send_update(self):
        self.change_incident(frp_series=[["2026-11-07T10:00:00Z", 164, "mtg"],
                                        ["2026-11-08T11:50:00Z", 51, "mtg"]],
                             clip_max_frp=164, clip_peak_time="2026-11-07T10:00:00Z")
        self.cycle()
        self.deliver.assert_not_called()
        self.render.assert_not_called()

    def test_legacy_missing_sent_strength_is_seeded_without_historical_replay(self):
        incident = self.incident()
        del incident["alerted_max_frp"]
        self.save_incident(incident)
        incident = self.cycle()
        self.deliver.assert_not_called()
        self.assertEqual(incident["alerted_max_frp"], 164)

    def test_failed_refresh_sends_text_then_later_video_without_repeating_rise(self):
        self.refresh_ok = False
        incident = self.cycle()
        self.assertEqual(incident["clip"], self.old_clip)
        self.assertEqual(incident["snap"], self.old_snap)
        self.assertTrue((self.cfg["_site_dir"] / self.old_clip).exists())
        self.assertIsNone(self.deliver.call_args.args[3])
        self.assertIn("ارتفاع شدة الحريق", self.deliver.call_args.args[2])
        self.assertEqual(incident["alerted_max_frp"], 164)
        self.assertTrue(incident["clip_video_pending"])
        self.refresh_ok = True
        self.now += dt.timedelta(minutes=10)
        incident = self.cycle()
        self.assertEqual(self.deliver.call_count, 2)
        self.assertEqual(self.deliver.call_args.args[3], self.cfg["_site_dir"] / "clips/fire-v164.mp4")
        self.assertIn("فيديو محدث للحريق", self.deliver.call_args.args[2])
        self.assertNotIn("ارتفاع شدة الحريق", self.deliver.call_args.args[2])
        self.assertNotIn("مؤكد الآن", self.deliver.call_args.args[2])
        self.assertFalse(incident["clip_video_pending"])
        self.cycle()
        self.assertEqual(self.deliver.call_count, 2)

    def test_failed_later_video_delivery_keeps_pending_until_success(self):
        self.refresh_ok = False
        self.cycle()
        self.refresh_ok = True
        self.deliver.return_value = None
        self.now += dt.timedelta(minutes=10)
        incident = self.cycle()
        self.assertTrue(incident["clip_video_pending"])
        self.assertEqual(incident["alerted_max_frp"], 164)
        self.deliver.return_value = 456
        self.now += dt.timedelta(minutes=3)
        incident = self.cycle()
        self.assertFalse(incident["clip_video_pending"])
        self.assertEqual(self.deliver.call_count, 3)

    def test_presentation_upgrade_preserves_previous_assets_until_replacement_exists(self):
        state_path = self.cfg["_state_dir"] / "state.json"
        state = load_json(state_path, {})
        state["clip_style"] = engine.CLIP_STYLE - 1
        save_json(state_path, state)
        self.refresh_ok = False

        incident = self.cycle()

        self.assertEqual(incident["clip"], self.old_clip)
        self.assertEqual(incident["snap"], self.old_snap)
        self.assertTrue((self.cfg["_site_dir"] / self.old_clip).exists())
        self.assertTrue(incident["clip_needs_refresh"])
        self.assertTrue(incident["clip_video_pending"])
        self.assertIsNone(self.deliver.call_args.args[3])
        published = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"][0]
        self.assertEqual(published["clip"], self.old_clip)
        self.assertTrue(published["clip_status_text"])

    def test_small_sea_fire_increase_sends_update(self):
        self.change_incident(max_frp=13.39, alerted_max_frp=2.41,
                             frp_series=[["2026-11-08T10:10:00Z", 2.41, "viirs"],
                                         ["2026-11-08T11:50:00Z", 13.39, "viirs"]],
                             clip_max_frp=2.41)
        incident = self.cycle()
        self.deliver.assert_called_once()
        self.assertIn("ارتفاع شدة الحريق", self.deliver.call_args.args[2])
        self.assertNotIn("متأخر", self.deliver.call_args.args[2])
        self.assertEqual(incident["alerted_max_frp"], 13.39)
        self.assertEqual(incident["clip_max_frp"], 13.39)

    def test_actual_oman_update_after_eleven_hours_is_labeled_and_sent_once(self):
        self.change_incident(first="2026-11-07T22:09:00Z", last="2026-11-07T23:05:00Z",
                             max_frp=13.39, alerted_max_frp=2.41,
                             alerted_at="2026-11-08T00:00:13Z", alert_updated_at="2026-11-08T00:00:13Z",
                             frp_series=[["2026-11-07T22:09:00Z", 2.4, "viirs"],
                                         ["2026-11-07T23:05:00Z", 13.4, "viirs"]],
                             polar=[{"t": "2026-11-07T23:05:00Z", "frp": 13.39}],
                             clip_max_frp=2.41, clip_peak_time="2026-11-07T22:09:00Z")
        incident = self.cycle()
        self.deliver.assert_called_once()
        message = self.deliver.call_args.args[2]
        self.assertIn("تحديث متأخر لقياسات الحريق", message)
        self.assertIn("2026-11-08 الساعة 02:05", message)
        self.assertIn("ليست رصداً جديداً الآن", message)
        self.assertEqual(self.deliver.call_args.kwargs["reply_to"], 123)
        self.assertIsNotNone(self.deliver.call_args.args[3])
        self.assertEqual(incident["alerted_max_frp"], 13.4)
        self.assertEqual(incident["clip_peak_time"], "2026-11-07T23:05:00Z")
        self.cycle()
        self.deliver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
