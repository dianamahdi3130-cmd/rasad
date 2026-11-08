"""Slow media workers cannot block scans, mutate alert state or publish obsolete clips."""
import copy
import datetime as dt
import fcntl
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from rasad import engine, mediaqueue
from rasad.brain import finalize, new_incident, add_to_incident
from rasad.updates import peak_reading
from rasad.util import UTC, iso, load_json, save_json


class MediaQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site")
        self.now = dt.datetime(2026, 11, 8, 16, tzinfo=UTC)
        det = {"src": "mtg_frp", "family": "mtg", "kind": "geo", "lat": 26.0,
               "lon": 56.0, "t": "2026-11-08T15:50:00Z", "frp": 13.4}
        self.inc = new_incident(det, "new")
        add_to_incident(self.inc, det)
        self.inc.update(place="sea", where="في البحر", country="عُمان", facility=None,
                        known_days=0, area_days=0)
        finalize(self.inc, self.cfg)
        self.state = SimpleNamespace(incidents=[self.inc], state={"untouched": True})
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network")
        self.session_patch = patch.object(mediaqueue, "make_session", return_value=self.session)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.render = Mock(side_effect=self.render_clip)
        self.render_patch = patch.object(engine, "add_clip", self.render)
        self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

    def render_clip(self, cfg, wms, state, inc, now, refresh=False):
        peak = peak_reading(inc)
        for field, suffix in (("clip", "mp4"), ("snap", "jpg")):
            name = f"clips/{inc['id']}-{peak[1]}.{suffix}"
            path = cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"rendered asset")
            inc[field] = name
        inc.update(clip_max_frp=peak[1], clip_peak_time=peak[0], clip_style=engine.CLIP_STYLE,
                   clip_updated_at=iso(now))
        inc.pop("clip_note", None)
        inc.pop("unseen_close", None)
        return True

    def enqueue(self, refresh=False):
        self.assertFalse(mediaqueue.request_clip(self.cfg, self.inc, self.now, refresh))
        return mediaqueue.queue_dir(self.cfg) / self.inc["media_job"]

    def old_media(self):
        for key, suffix in (("clip", "mp4"), ("snap", "jpg")):
            self.inc[key] = f"clips/old.{suffix}"
            file = self.cfg["_site_dir"] / self.inc[key]
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"old asset")
        self.inc.update(clip_max_frp=2, clip_peak_time="2026-11-08T14:30:00Z",
                        clip_style=engine.CLIP_STYLE)

    def test_enqueue_does_not_render_or_change_alert_and_attempt_state(self):
        self.inc.update(status="alerted", alerted_max_frp=2, tg_msg=123)
        folder = self.enqueue()
        self.assertTrue((folder / "job.json").is_file())
        self.assertEqual(folder.parent, self.root / ".rasad-media")
        self.render.assert_not_called()
        self.assertNotIn("clip_tried", self.inc)
        self.assertEqual((self.inc["status"], self.inc["alerted_max_frp"], self.inc["tg_msg"]),
                         ("alerted", 2, 123))
        self.session.get.assert_not_called()

    def test_repeated_request_is_idempotent_and_worker_writes_staging_only(self):
        folder = self.enqueue()
        token = self.inc["media_job"]
        self.enqueue()
        self.assertEqual(self.inc["media_job"], token)
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
        self.render.assert_called_once()
        self.assertFalse(self.cfg["_site_dir"].exists())
        self.assertEqual(self.state.state, {"untouched": True})
        self.assertNotIn("clip", self.inc)
        self.assertTrue(load_json(folder / "result.json", {})["ok"])

    def test_complete_matching_pair_imports_then_ready_request_skips_queue(self):
        self.old_media()
        folder = self.enqueue(refresh=True)
        mediaqueue.process_one(self.cfg, self.now)
        self.assertEqual(self.inc["clip"], "clips/old.mp4")
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 1)
        self.assertEqual(self.inc["clip_max_frp"], 13.4)
        self.assertEqual((self.cfg["_site_dir"] / self.inc["clip"]).read_bytes(), b"rendered asset")
        self.assertEqual((self.cfg["_site_dir"] / self.inc["snap"]).read_bytes(), b"rendered asset")
        self.assertNotIn("media_job", self.inc)
        self.assertTrue(mediaqueue.request_clip(self.cfg, self.inc, self.now, refresh=True))
        self.assertFalse(folder.exists(), "consumed staging copies must not accumulate")

    def test_new_measurement_before_import_discards_stale_result_without_losing_old_media(self):
        self.old_media()
        folder = self.enqueue(refresh=True)
        mediaqueue.process_one(self.cfg, self.now)
        self.inc["max_frp"] = 20
        self.inc["frp_series"].append(["2026-11-08T16:00:00Z", 20, "mtg"])
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        self.assertEqual((self.inc["clip"], self.inc["clip_max_frp"]), ("clips/old.mp4", 2))
        self.assertEqual((self.cfg["_site_dir"] / self.inc["clip"]).read_bytes(), b"old asset")
        self.assertFalse(folder.exists(), "obsolete staged results must be removed")

    def test_worker_skips_superseded_unstarted_revision(self):
        first = self.enqueue()
        self.inc["max_frp"] = 20
        self.inc["frp_series"].append(["2026-11-08T16:00:00Z", 20, "mtg"])
        second = self.enqueue()
        self.assertNotEqual(first, second)
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse((first / "result.json").exists())
        self.assertFalse(first.exists())
        self.assertTrue((second / "result.json").exists())

    def test_failed_refresh_keeps_old_media_and_retry_waits_ten_minutes(self):
        self.old_media()
        folder = self.enqueue(refresh=True)
        self.render.side_effect = None
        self.render.return_value = False
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        self.assertEqual((self.inc["clip"], self.inc["snap"]), ("clips/old.mp4", "clips/old.jpg"))
        self.assertEqual(self.inc["clip_refresh_tried"], iso(self.now))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=9)))
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=10)))
        self.assertEqual(self.render.call_count, 2)
        self.assertFalse(load_json(folder / "result.json", {})["ok"])

    def test_failed_first_render_note_is_imported_and_retries_after_ten_minutes(self):
        def invisible(cfg, wms, state, inc, now, refresh=False):
            inc["clip_note"] = "لا يظهر بوضوح"
            inc["unseen_close"] = True
            return False
        self.render.side_effect = invisible
        self.inc["srcs"] = ["mtg_spot"]
        self.enqueue()
        mediaqueue.process_one(self.cfg, self.now)
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        self.assertEqual(self.inc["clip_note"], "لا يظهر بوضوح")
        self.assertTrue(self.inc["unseen_close"])
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=9)))
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=10)))

    def test_first_attempt_retry_interval_is_configurable(self):
        self.cfg["clip"]["initial_retry_min"] = 3
        self.render.side_effect = None
        self.render.return_value = False
        folder = self.enqueue()
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=2)))
        self.assertTrue(folder.exists(), "failed jobs keep their retry state")
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now + dt.timedelta(minutes=3)))

    def test_partial_or_unsafe_result_does_not_replace_either_reference(self):
        self.old_media()
        folder = self.enqueue(refresh=True)
        mediaqueue.process_one(self.cfg, self.now)
        result = load_json(folder / "result.json", {})
        (folder / "site" / result["fields"]["snap"]).unlink()
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        result["fields"]["clip"] = "../outside.mp4"
        save_json(folder / "result.json", result)
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        self.assertEqual((self.inc["clip"], self.inc["snap"]), ("clips/old.mp4", "clips/old.jpg"))

    def test_worker_lock_prevents_duplicate_render_and_recovers_when_released(self):
        folder = self.enqueue()
        with open(folder / "render.lock", "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
            self.render.assert_not_called()
        self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.render.assert_called_once()

    def test_consumer_never_waits_for_a_worker_result_lock(self):
        folder = self.enqueue()
        mediaqueue.process_one(self.cfg, self.now)
        with open(folder / "render.lock", "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
            self.assertNotIn("clip", self.inc)
            self.assertTrue(folder.exists())
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 1)
        self.assertFalse(folder.exists())

    def test_obsolete_config_is_cleaned_without_rendering_or_busy_worker_result(self):
        folder = self.enqueue()
        self.cfg["clip"]["fps"] += 1
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse(folder.exists())
        self.render.assert_not_called()
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))

    def test_unwritten_request_has_a_short_grace_period_then_is_cleaned(self):
        folder = mediaqueue.queue_dir(self.cfg) / ("a" * 64)
        folder.mkdir(parents=True)
        stamp = self.now.timestamp()
        os.utime(folder, (stamp, stamp))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
        self.assertTrue(folder.exists())
        os.utime(folder, (stamp - 61, stamp - 61))
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))
        self.assertFalse(folder.exists())
        self.render.assert_not_called()

    def test_missing_staging_folder_does_not_crash_scanner_or_worker(self):
        self.inc["media_job"] = "b" * 64
        self.assertEqual(mediaqueue.consume_results(self.cfg, self.state, self.now), 0)
        self.assertFalse(mediaqueue.process_one(self.cfg, self.now))

    def test_only_media_fields_import_not_worker_status_or_notification_state(self):
        self.inc.update(status="alerted", alerted_max_frp=2, tg_msg=123)
        folder = self.enqueue()
        mediaqueue.process_one(self.cfg, self.now)
        result = load_json(folder / "result.json", {})
        result["fields"].update(status="held", alerted_max_frp=99, tg_msg=999)
        save_json(folder / "result.json", result)
        mediaqueue.consume_results(self.cfg, self.state, self.now)
        self.assertEqual((self.inc["status"], self.inc["alerted_max_frp"], self.inc["tg_msg"]),
                         ("alerted", 2, 123))

    def test_disabled_video_does_not_queue(self):
        self.cfg["clip"]["enabled"] = False
        self.assertFalse(mediaqueue.request_clip(self.cfg, self.inc, self.now))
        self.assertFalse(mediaqueue.queue_dir(self.cfg).exists())


if __name__ == "__main__":
    unittest.main()
