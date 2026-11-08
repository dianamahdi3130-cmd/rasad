"""Offline regressions for evolving fire videos and cloud-safe revisions."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rasad import engine
from rasad.clip import NotVisible
from rasad.util import iso, parse_time


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


if __name__ == "__main__":
    unittest.main()
