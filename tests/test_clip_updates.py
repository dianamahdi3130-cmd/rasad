"""Offline regressions for evolving fire videos and cloud-safe revisions."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory
from rasad.clip import NotVisible
from rasad.util import iso, load_json, parse_time, save_json


class ClipRefreshTests(unittest.TestCase):
    NOW = parse_time("2026-11-08T12:05:00Z")

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["_site_dir"] = Path(self.folder.name)
        self.state = SimpleNamespace(state={})
        self.inc = {
            "id": "test-fire", "lat": 28.3065, "lon": 36.5093,
            "first": "2026-11-08T08:00:00Z", "last": "2026-11-08T11:00:00Z",
            "where": "غرب تبوك (السعودية)", "country": "السعودية", "place": "land",
            "verdict": "مؤكد", "max_frp": 164.0,
            "frp_series": [["2026-11-08T08:20:00Z", 51.0, "mtg"],
                           ["2026-11-08T11:00:00Z", 164.0, "mtg"]],
            "polar": [], "clip": "clips/old.mp4", "snap": "clips/old.jpg",
        }
        self.layer = {"name": "test:fire", "cadence_min": 10}

    def render(self, **kwargs):
        def output(cfg, wms, lat, lon, t_end, base, **kw):
            return Path(str(base) + ".mp4"), Path(str(base) + ".jpg")
        with patch.object(engine, "clip_layers", return_value=[self.layer]), \
                patch.object(engine, "latest_time", return_value=kwargs.pop("latest", parse_time("2026-11-08T12:00:00Z"))), \
                patch.object(engine, "utcnow", return_value=self.NOW), \
                patch.object(engine, "make_clip", side_effect=output) as make:
            result = engine.clip_for(self.cfg, Mock(), self.state, self.inc, **kwargs)
        return result, make

    def test_late_peak_is_included_beyond_old_ninety_minute_limit(self):
        _, make = self.render(focus_time="2026-11-08T11:00:00Z")
        args, kwargs = make.call_args
        self.assertEqual(args[4], parse_time("2026-11-08T11:10:00Z"))
        self.assertEqual(kwargs["frames"], 16)
        self.assertIn(["2026-11-08T11:00:00Z", 164.0], kwargs["info"]["frp"])
        self.assertIn(["2026-11-08T08:20:00Z", 51.0], kwargs["info"]["frp"])

    def test_short_clip_keeps_frames_before_fire_and_reaches_peak(self):
        self.inc["first"] = "2026-11-08T10:00:00Z"
        self.inc["frp_series"] = [["2026-11-08T10:50:00Z", 164.0, "mtg"]]
        _, make = self.render()
        end = make.call_args.args[4]
        count = make.call_args.kwargs["frames"]
        beginning = end - dt.timedelta(minutes=10 * (count - 1))
        self.assertLess(beginning, parse_time(self.inc["first"]))
        self.assertGreaterEqual(end, parse_time("2026-11-08T10:50:00Z"))

    def test_peak_from_polar_data_is_not_hidden_by_an_existing_european_series(self):
        self.inc["polar"] = [{"t": "2026-11-08T11:20:00Z", "frp": 181.0}]
        self.inc["max_frp"] = 181.0
        _, make = self.render()
        self.assertEqual(make.call_args.args[4], parse_time("2026-11-08T11:30:00Z"))
        self.assertIn(["2026-11-08T11:20:00Z", 181.0], make.call_args.kwargs["info"]["frp"])
        self.assertIn(["2026-11-08T11:00:00Z", 164.0], make.call_args.kwargs["info"]["frp"])

    def test_changed_measurement_and_presentation_get_distinct_cloud_filenames(self):
        initial, _ = self.render()
        self.inc["frp_series"][-1][1] = 181.0
        changed, _ = self.render()
        self.assertNotEqual(initial[0], changed[0])
        self.inc["where"] = "تبوك (السعودية)"
        relabeled, _ = self.render()
        self.assertNotEqual(changed[0], relabeled[0])
        unchanged, _ = self.render()
        self.assertEqual(relabeled, unchanged)

    def test_new_measurement_cannot_use_picture_from_before_its_slot(self):
        with patch.object(engine, "clip_layers", return_value=[self.layer]), \
                patch.object(engine, "latest_time", return_value=parse_time("2026-11-08T10:50:00Z")), \
                patch.object(engine, "utcnow", return_value=self.NOW), \
                patch.object(engine, "make_clip") as make:
            with self.assertRaisesRegex(RuntimeError, "new measurement"):
                engine.clip_for(self.cfg, Mock(), self.state, self.inc,
                                focus_time="2026-11-08T11:00:00Z")
        make.assert_not_called()

    def test_successful_refresh_replaces_both_assets_and_records_covered_peak(self):
        output = (self.cfg["_site_dir"] / "clips/new.mp4", self.cfg["_site_dir"] / "clips/new.jpg")
        with patch.object(engine, "clip_for", return_value=output) as render:
            self.assertTrue(engine.add_clip(self.cfg, Mock(), self.state, self.inc, self.NOW, refresh=True))
        self.assertEqual(self.inc["clip"], "clips/new.mp4")
        self.assertEqual(self.inc["snap"], "clips/new.jpg")
        self.assertEqual(self.inc["clip_peak_time"], "2026-11-08T11:00:00Z")
        self.assertEqual(self.inc["clip_max_frp"], 164.0)
        self.assertEqual(self.inc["clip_updated_at"], iso(self.NOW))
        self.assertEqual(render.call_args.kwargs["focus_time"], "2026-11-08T11:00:00Z")

    def test_failed_refresh_preserves_previous_video_and_snapshot(self):
        for failure in (RuntimeError("download failed"), NotVisible("clouds")):
            with self.subTest(failure=type(failure).__name__):
                incident = copy.deepcopy(self.inc)
                incident["srcs"] = ["mtg_spot"]
                with patch.object(engine, "clip_for", side_effect=failure):
                    self.assertFalse(engine.add_clip(self.cfg, Mock(), self.state, incident,
                                                     self.NOW, refresh=True))
                self.assertEqual(incident["clip"], "clips/old.mp4")
                self.assertEqual(incident["snap"], "clips/old.jpg")
                self.assertNotIn("unseen_close", incident)
                self.assertNotIn("clip_updated_at", incident)

    def test_refresh_has_short_retry_cooldown_and_does_not_use_initial_attempt_timer(self):
        self.inc["clip_tried"] = iso(self.NOW)
        with patch.object(engine, "clip_for", side_effect=RuntimeError("not ready")) as render:
            self.assertFalse(engine.add_clip(self.cfg, Mock(), self.state, self.inc, self.NOW, refresh=True))
            self.assertFalse(engine.add_clip(self.cfg, Mock(), self.state, self.inc,
                                             self.NOW + dt.timedelta(minutes=9), refresh=True))
            self.assertFalse(engine.add_clip(self.cfg, Mock(), self.state, self.inc,
                                             self.NOW + dt.timedelta(minutes=10), refresh=True))
        self.assertEqual(render.call_count, 2)

    def test_retry_reuses_fresh_local_media_without_rendering_it_again(self):
        self.inc.update(clip_max_frp=164.0, clip_peak_time="2026-11-08T11:00:00Z",
                        clip_style=engine.CLIP_STYLE, clip_refresh_tried=iso(self.NOW))
        video = self.cfg["_site_dir"] / self.inc["clip"]
        video.parent.mkdir(parents=True)
        video.write_bytes(b"rendered")
        with patch.object(engine, "clip_for") as render:
            self.assertTrue(engine.add_clip(self.cfg, Mock(), self.state, self.inc, self.NOW, refresh=True))
        render.assert_not_called()

    def test_cloud_only_media_reference_does_not_count_as_local_fresh_video(self):
        self.inc.update(clip_max_frp=164.0, clip_peak_time="2026-11-08T11:00:00Z",
                        clip_style=engine.CLIP_STYLE)
        with patch.object(engine, "clip_for", side_effect=RuntimeError("not ready")) as render:
            self.assertFalse(engine.add_clip(self.cfg, Mock(), self.state, self.inc, self.NOW, refresh=True))
        render.assert_called_once()

    def test_refresh_without_aggregate_records_actual_peak_and_stops_repeating(self):
        self.inc["max_frp"] = None
        output = (self.cfg["_site_dir"] / "clips/new.mp4", self.cfg["_site_dir"] / "clips/new.jpg")
        with patch.object(engine, "clip_for", return_value=output):
            self.assertTrue(engine.add_clip(self.cfg, Mock(), self.state, self.inc, self.NOW, refresh=True))
        self.assertEqual(self.inc["clip_max_frp"], 164.0)
        self.assertFalse(engine._clip_measurement_changed(self.inc))


class WebsiteClipRefreshIntegrationTests(unittest.TestCase):
    """The website follows measured heat even when no Telegram update is eligible."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = parse_time("2026-11-08T09:40:00Z")
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        self.cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 25, 57, 27]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["alerts"]["intensity_add_mw"] = 25
        self.cfg["clip"]["per_run"] = 1
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.old_clip = "clips/oman-old.mp4"
        self.old_snap = "clips/oman-old.jpg"
        for name in (self.old_clip, self.old_snap):
            path = self.cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old media")
        self.incident = {
            "id": "oman", "kind": "new", "first": "2026-11-07T22:09:00Z",
            "last": "2026-11-07T23:05:00Z", "lat": 26.1776, "lon": 56.0835,
            "best": "polar", "families": ["viirs", "mtg"],
            "srcs": ["VIIRS_NOAA21_NRT", "mtg_spot"], "geo_slots": [], "polar": [],
            "max_frp": 13.39, "frp_series": [["2026-11-07T22:09:00Z", 2.4, "viirs"],
                                             ["2026-11-07T23:05:00Z", 13.4, "viirs"]],
            "n": 4, "status": "alerted", "verdict": "مؤكد", "alerted_verdict": "مؤكد",
            "alerted_max_frp": 2.41, "alerted_at": "2026-11-08T00:00:13Z",
            "alert_updated_at": "2026-11-08T00:00:13Z", "known_days": 0, "area_days": 0,
            "clip": self.old_clip, "snap": self.old_snap, "clip_max_frp": 2.41,
            "clip_peak_time": "2026-11-07T22:09:00Z", "clip_style": engine.CLIP_STYLE,
            "clip_refresh_tried": "2026-11-08T00:39:08Z", "place": "sea", "country": "عُمان",
            "facility": None, "priority": "high", "_pos": [26.178, 56.084, "offline-map"]}
        self.save(self.incident)
        session = Mock()
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No network in clip tests")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر قرب مضيق هرمز", "عُمان")
        self.deliver = Mock(side_effect=AssertionError("No Telegram update should be eligible"))
        self.render = Mock(side_effect=self.make_media)
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now), make_session=Mock(return_value=session),
            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
            latest_time=Mock(side_effect=AssertionError("No source requests")),
            clip_for=self.render, deliver=self.deliver)
        patches.start()
        self.addCleanup(patches.stop)

    def save(self, incident):
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])

    def make_media(self, cfg, wms, state, incident, focus_time=None):
        video = cfg["_site_dir"] / "clips/oman-new.mp4"
        snap = cfg["_site_dir"] / "clips/oman-new.jpg"
        video.write_bytes(b"new media")
        snap.write_bytes(b"new still")
        return video, snap

    def cycle(self):
        state = engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_not_called()
        return state.incidents[0]

    def test_old_oman_event_refreshes_website_without_an_eligible_telegram_update(self):
        incident = self.cycle()
        self.render.assert_called_once()
        self.assertEqual(self.render.call_args.kwargs["focus_time"], "2026-11-07T23:05:00Z")
        self.assertEqual(incident["clip"], "clips/oman-new.mp4")
        self.assertEqual(incident["clip_peak_time"], "2026-11-07T23:05:00Z")
        self.assertEqual(incident["clip_max_frp"], 13.39)
        self.assertNotIn("clip_needs_refresh", incident)
        self.assertEqual(incident["alerted_max_frp"], 2.41)
        published = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"][0]
        self.assertEqual(published["clip"], "clips/oman-new.mp4")
        self.cycle()
        self.render.assert_called_once()

    def test_failed_automatic_refresh_preserves_old_assets_and_retries_after_cooldown(self):
        self.render.side_effect = RuntimeError("pictures unavailable")
        incident = self.cycle()
        self.assertEqual(incident["clip"], self.old_clip)
        self.assertEqual(incident["snap"], self.old_snap)
        self.assertTrue(incident["clip_needs_refresh"])
        self.assertTrue((self.cfg["_site_dir"] / self.old_clip).exists())
        self.now += dt.timedelta(minutes=9)
        self.cycle()
        self.assertEqual(self.render.call_count, 1)
        self.now += dt.timedelta(minutes=1)
        self.render.side_effect = self.make_media
        incident = self.cycle()
        self.assertEqual(self.render.call_count, 2)
        self.assertEqual(incident["clip"], "clips/oman-new.mp4")
        self.assertNotIn("clip_needs_refresh", incident)

    def test_rounding_only_difference_does_not_regenerate_current_clip(self):
        self.incident.update(clip_peak_time="2026-11-07T23:05:00Z", clip_max_frp=13.39)
        self.save(self.incident)
        self.cycle()
        self.render.assert_not_called()

    def test_exhausted_render_budget_still_marks_stale_video_pending(self):
        self.cfg["clip"]["per_run"] = 0
        incident = self.cycle()
        self.render.assert_not_called()
        self.assertTrue(incident["clip_needs_refresh"])
        published = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"][0]
        self.assertTrue(published["clip_needs_refresh"])
        self.assertTrue(published["clip_status_text"])


if __name__ == "__main__":
    unittest.main()
