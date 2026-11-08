"""The scan publishes and alerts without waiting for media or replay work."""
import copy
import datetime as dt
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from rasad import engine, mediaqueue
from rasad.brain import Memory
from rasad.updates import intensity_update_due, peak_reading
from rasad.util import UTC, iso, load_json, save_json


class AsyncEngineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site",
                        _geodata_dir=self.root / "geo", _background_media=True)
        self.cfg["regions"] = [{"id": "gulf", "name": "الخليج", "bbox": [55, 25, 57, 27]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": iso(self.now)})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.save_incident({
            "id": "fire", "kind": "new", "first": "2026-11-08T11:40:00Z",
            "last": "2026-11-08T11:50:00Z", "lat": 26.0, "lon": 56.0, "best": "geo",
            "families": ["mtg"], "srcs": ["mtg_frp"],
            "geo_slots": ["2026-11-08T11:40:00Z", "2026-11-08T11:50:00Z"],
            "polar": [], "max_frp": 13.4, "n": 2, "status": "pending",
            "alerted_verdict": None, "known_days": 0, "area_days": 0,
            "frp_series": [["2026-11-08T11:50:00Z", 13.4, "mtg"]],
            "place": "sea", "where": "في البحر", "facility": None, "country": "عُمان", "priority": "high",
            "_pos": [26.0, 56.0, "offline-map"]})
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر", "عُمان")
        self.deliver = Mock(return_value=123)
        self.render = Mock(side_effect=AssertionError("The scanner must not render"))
        self.source = Mock(side_effect=AssertionError("No source request expected"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now),
            make_session=Mock(return_value=self.session), WMS=Mock(), Geo=Mock(return_value=geo),
            Availability=Mock(), latest_time=self.source, detect_geo_layer=self.source,
            firms_fetch=self.source, add_clip=self.render, deliver=self.deliver,
            tg_send=Mock(side_effect=AssertionError("No direct Telegram request")))
        patches.start()
        self.addCleanup(patches.stop)

    def incident(self):
        return load_json(self.cfg["_state_dir"] / "incidents.json", [])[0]

    def save_incident(self, incident):
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])

    def cycle(self):
        result = engine.run_cycle(self.cfg, send=True)
        self.render.assert_not_called()
        self.source.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
        return result

    def finish_media(self):
        """Exercise the real worker/consumer boundary with an offline renderer."""
        def render(cfg, wms, state, incident, now, refresh=False):
            peak = peak_reading(incident)
            for key, suffix in (("clip", "mp4"), ("snap", "jpg")):
                name = f"clips/{incident['id']}-v{peak[1]:g}.{suffix}"
                target = cfg["_site_dir"] / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"finished media")
                incident[key] = name
            incident.update(clip_max_frp=peak[1], clip_peak_time=peak[0],
                            clip_updated_at=iso(now), clip_style=engine.CLIP_STYLE)
            incident.pop("clip_needs_refresh", None)
            return True
        session = MagicMock()
        session.__enter__.return_value = session
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No worker network")
        with patch.object(mediaqueue, "make_session", return_value=session), \
                patch.object(engine, "add_clip", side_effect=render):
            self.assertTrue(mediaqueue.process_one(self.cfg, now=self.now))

    def test_first_alert_is_text_then_finished_video_follows_same_thread_once(self):
        self.cycle()
        self.deliver.assert_called_once()
        self.assertIsNone(self.deliver.call_args.args[3])
        self.assertTrue(self.incident()["clip_video_pending"])
        self.assertEqual(self.incident()["tg_msg"], 123)
        self.assertTrue(self.incident().get("media_job"))

        self.now += dt.timedelta(minutes=1)
        self.finish_media()
        self.now += dt.timedelta(minutes=1)
        self.cycle()
        self.assertEqual(self.deliver.call_count, 2)
        post = self.deliver.call_args
        self.assertEqual(post.kwargs["reply_to"], 123)
        self.assertEqual(post.args[3], self.cfg["_site_dir"] / "clips/fire-v13.4.mp4")
        self.assertTrue(post.args[3].is_file())
        self.assertIn("فيديو محدث", post.args[2])
        self.assertFalse(self.incident()["clip_video_pending"])
        self.cycle()
        self.assertEqual(self.deliver.call_count, 2)

    def test_first_video_retry_and_website_request_share_one_queued_revision(self):
        self.cycle()
        token = self.incident()["media_job"]
        self.assertTrue(self.incident()["clip_video_pending"])
        self.cycle()  # pending retry asks for a refresh; the website asks for first media
        self.assertEqual(self.incident()["media_job"], token)
        folders = [p for p in mediaqueue.queue_dir(self.cfg).iterdir()
                   if p.is_dir() and (p / "job.json").is_file()]
        self.assertEqual(len(folders), 1)
        self.assertFalse(load_json(folders[0] / "job.json", {})["refresh"])
        self.deliver.assert_called_once()

    def test_strength_text_never_attaches_old_video_while_new_render_is_pending(self):
        incident = self.incident()
        incident.update(status="alerted", alerted_verdict="مرجّح", alerted_max_frp=2,
                        alerted_at="2026-11-08T11:00:00Z", alert_updated_at="2026-11-08T11:00:00Z",
                        tg_msg=456, clip="clips/old-v2.mp4", snap="clips/old-v2.jpg",
                        clip_max_frp=2, clip_peak_time="2026-11-08T11:40:00Z",
                        clip_style=engine.CLIP_STYLE)
        for key in ("clip", "snap"):
            path = self.cfg["_site_dir"] / incident[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old media")
        self.save_incident(incident)

        self.cycle()
        self.deliver.assert_called_once()
        post = self.deliver.call_args
        self.assertIn("ارتفاع شدة الحريق", post.args[2])
        self.assertIsNone(post.args[3])
        self.assertEqual(post.kwargs["reply_to"], 456)
        self.assertEqual(self.incident()["clip"], "clips/old-v2.mp4")
        self.assertTrue(self.incident()["clip_video_pending"])

        self.now += dt.timedelta(minutes=1)
        self.finish_media()
        self.now += dt.timedelta(minutes=1)
        self.cycle()
        self.assertEqual(self.deliver.call_count, 2)
        self.assertEqual(self.deliver.call_args.args[3], self.cfg["_site_dir"] / "clips/fire-v13.4.mp4")
        self.assertFalse(self.incident()["clip_video_pending"])

    def test_historical_recovery_is_visible_without_news_alert_even_when_recent(self):
        save_json(self.cfg["_state_dir"] / "incidents.json", [])
        rows = [{"src": "mtg_frp", "family": "mtg", "kind": "geo", "lat": 26.0,
                 "lon": 56.0, "t": f"2026-11-08T11:{minute}:00Z", "frp": 63.0,
                 "_recovered": True, "_no_alert": True} for minute in ("20", "30", "40")]
        recovery = {"enabled": True, "checked": 3, "detections": 3, "pending": 0,
                    "missing": 0, "historical": True}
        with patch.object(engine.backfill, "collect", return_value=(rows, recovery)):
            result = self.cycle()
        self.assertEqual(len(result.incidents), 1)
        self.assertTrue(result.incidents[0]["recovered_historical"])
        self.assertEqual(result.incidents[0]["max_frp"], 63)
        self.deliver.assert_not_called()
        published = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"]
        self.assertTrue(published[0]["recovered_historical"])
        self.assertTrue(published[0]["headline"].startswith("رصد سابق مسترجع: "))

    def test_uncertain_association_blocks_first_alert_and_ready_pending_video(self):
        self.cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
        original = self.incident()
        for pending_video in (False, True):
            with self.subTest(pending_video=pending_video):
                incident = copy.deepcopy(original)
                incident.update(association_uncertain=True,
                                association={"incident_id": "earlier-fire"})
                if pending_video:
                    incident.update(status="alerted", alerted_verdict="مرجّح", alerted_max_frp=2,
                                    alerted_at="2026-11-08T11:00:00Z",
                                    alert_updated_at="2026-11-08T11:00:00Z", tg_msg=456,
                                    clip_video_pending=True, clip="clips/ready.mp4", snap="clips/ready.jpg",
                                    clip_max_frp=13.4, clip_peak_time="2026-11-08T11:50:00Z",
                                    clip_style=engine.CLIP_STYLE)
                    for key in ("clip", "snap"):
                        path = self.cfg["_site_dir"] / incident[key]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"ready, but its incident is still ambiguous")
                self.save_incident(incident)
                self.cycle()
                self.deliver.assert_not_called()
                saved = self.incident()
                self.assertEqual(saved["verdict"], "يحتاج تأكيداً")
                self.assertIn("بقعة حرارية", saved["headline"])
                if pending_video:
                    self.assertTrue(saved["clip_video_pending"])
                    self.assertEqual(saved["alerted_max_frp"], 2)
                else:
                    self.assertIsNone(saved["alerted_verdict"])

    def test_notification_deadline_defers_update_without_consuming_sent_baseline(self):
        first = self.incident()
        first.update(status="alerted", alerted_verdict="مرجّح", alerted_max_frp=2,
                     alerted_at="2026-11-08T11:00:00Z", alert_updated_at="2026-11-08T11:00:00Z",
                     tg_msg=456)
        second = copy.deepcopy(first)
        second.update(id="fire-2", lat=26.2, lon=56.2, tg_msg=789)
        save_json(self.cfg["_state_dir"] / "incidents.json", [first, second])
        elapsed = [0.0]

        def first_post_uses_the_time_budget(*args, **kwargs):
            elapsed[0] = 21.0
            return 123

        self.deliver.side_effect = first_post_uses_the_time_budget
        with patch.object(engine.time, "monotonic", side_effect=lambda: elapsed[0]):
            result = self.cycle()
        self.deliver.assert_called_once()
        saved = {i["id"]: i for i in result.incidents}
        self.assertEqual(saved["fire"]["alerted_max_frp"], 13.4)
        self.assertEqual(saved["fire-2"]["alerted_max_frp"], 2)
        self.assertEqual(saved["fire-2"]["alert_updated_at"], "2026-11-08T11:00:00Z")
        self.assertTrue(intensity_update_due(saved["fire-2"], self.cfg, self.now))

        self.deliver.side_effect = None
        self.now += dt.timedelta(minutes=1)
        result = self.cycle()
        self.assertEqual(self.deliver.call_count, 2)
        self.assertEqual(self.deliver.call_args.kwargs["reply_to"], 789)
        saved = {i["id"]: i for i in result.incidents}
        self.assertEqual(saved["fire-2"]["alerted_max_frp"], 13.4)

    def test_published_scan_time_is_completion_and_keeps_start_and_duration(self):
        started = self.now
        finished = self.now + dt.timedelta(seconds=31)
        with patch.object(engine, "utcnow", side_effect=[started, finished]), \
                patch.object(engine.time, "monotonic", side_effect=itertools.chain([10.0], itertools.repeat(41.0))):
            self.cycle()
        meta = load_json(self.cfg["_site_dir"] / "data" / "meta.json", {})
        self.assertEqual(meta["scan_started"], iso(started))
        self.assertEqual(meta["scan_finished"], iso(finished))
        self.assertEqual(meta["generated"], iso(finished))
        self.assertEqual(meta["scan_duration_s"], 31)

    def test_pending_recent_tiles_remain_explicitly_partial(self):
        status = {"mtg_frp": {"name": "MTG", "ok": True,
                              "latest": "2026-11-08T11:50:00Z", "checked": 2,
                              "missing": 0, "pending_recent": 12, "scan_complete": False}}
        with patch.object(engine.geocollect, "collect", return_value=([], status, 2)):
            self.cycle()
        meta = load_json(self.cfg["_site_dir"] / "data" / "meta.json", {})
        self.assertTrue(meta["scan_partial"])
        self.assertEqual(meta["sources"]["mtg_frp"]["latest"], "2026-11-08T11:50:00Z")


if __name__ == "__main__":
    unittest.main()
