"""First-slot alerts cross the worker boundary without entering fire memory."""
import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from rasad import engine, mediaqueue
from rasad.brain import Memory, add_to_incident, finalize, new_incident
from rasad.clip import NotVisible
from rasad.util import UTC, iso, load_json, save_json


class EarlyEngineTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = dt.datetime(2026, 11, 13, 19, 5, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site",
                        _geodata_dir=self.root / "geo", _background_media=True)
        self.cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز",
                                "bbox": [55.4, 25.3, 57.6, 27.3]}]
        self.cfg["seawatch"].update(enabled=True, alerts=True, look_back=False)
        self.cfg["seawatch"]["daytime"]["enabled"] = False
        self.cfg["seawatch"]["early_alerts"]["enabled"] = True
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["firms"]["map_key"] = "offline-test-key"
        self.cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
        for feature in ("satpic", "watch", "smoke", "flares", "recovery"):
            self.cfg[feature]["enabled"] = False
        self.candidate = {"src": "mtg_spot", "t": "2026-11-13T18:50:00Z",
                          "lat": 26.3716, "lon": 56.2988, "rise": 53.3}
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": iso(self.now),
            "bootstrapped": iso(self.now), "sea_candidates": [self.candidate]})
        memory = Memory({})
        memory.record(30, 48, "2026-11-12", 10)
        self.original_memory = copy.deepcopy(memory.cells)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No scanner network")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر قرب مضيق هرمز", "عُمان")
        self.delivery = Mock(return_value=123)
        self.render = Mock(side_effect=AssertionError("Scanner must never render media"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now),
            make_session=Mock(return_value=self.session), WMS=Mock(), Geo=Mock(return_value=geo),
            Availability=Mock(), latest_time=Mock(side_effect=AssertionError("No latest-time fetch")),
            firms_fetch=Mock(return_value=[]), add_clip=self.render, deliver=self.delivery,
            tg_send=Mock(side_effect=AssertionError("No actual Telegram call")))
        patches.start()
        self.addCleanup(patches.stop)
        watch_patch = patch.object(engine.seawatch, "watch", return_value=[])
        watch_patch.start()
        self.addCleanup(watch_patch.stop)

    def cycle(self, *, send=True):
        result = engine.run_cycle(self.cfg, send=send)
        self.render.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
        return result

    def finish_media(self):
        def render(cfg, wms, state, inc, now, refresh=False):
            for key, suffix in (("clip", "mp4"), ("snap", "jpg")):
                name = f"clips/{inc['id']}-first.{suffix}"
                target = cfg["_site_dir"] / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"offline rendered media")
                inc[key] = name
            inc.update(clip_max_frp=None, clip_peak_time=None,
                       clip_updated_at=iso(now), clip_style=engine.CLIP_STYLE)
            inc.pop("clip_note", None)
            return True
        session = MagicMock()
        session.__enter__.return_value = session
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No worker network")
        with patch.object(mediaqueue, "make_session", return_value=session), \
                patch.object(engine, "add_clip", side_effect=render):
            self.assertTrue(mediaqueue.process_one(self.cfg, self.now))

    def public_items(self):
        return load_json(self.cfg["_site_dir"] / "data/incidents.json", {})["items"]

    def test_one_slot_alerts_and_queues_without_incident_or_memory_writes(self):
        state = self.cycle()
        self.assertEqual(state.incidents, [])
        self.assertEqual(state.memory, self.original_memory)
        self.delivery.assert_called_once()
        text, media = self.delivery.call_args.args[2:4]
        self.assertIn("أولي", text)
        self.assertIn("تأكيد", text)
        self.assertNotIn("53.3", text)
        self.assertNotIn(" MW", text)
        self.assertIsNone(media)
        self.assertTrue(state.sent)
        jobs = list(mediaqueue.queue_dir(self.cfg).glob("*/job.json"))
        self.assertEqual(len(jobs), 1)
        snapshot = load_json(jobs[0], {})["incident"]
        self.assertTrue(snapshot["preliminary"])
        self.assertEqual(snapshot["first"], self.candidate["t"])
        self.assertIsNone(snapshot["max_frp"])
        items = self.public_items()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["preliminary"])
        self.assertNotIn("حريق", items[0]["headline"])

    def test_ready_video_follows_original_post_once_across_scanner_restarts(self):
        self.cycle()
        original_id = self.public_items()[0]["id"]
        self.finish_media()
        self.now += dt.timedelta(minutes=2)
        second = self.cycle()
        self.assertEqual(self.delivery.call_count, 2)
        video_call = self.delivery.call_args
        self.assertEqual(video_call.kwargs["reply_to"], 123)
        self.assertIn("أولي", video_call.args[2])
        self.assertTrue(video_call.args[3].is_file())
        self.assertEqual(video_call.args[3].suffix, ".mp4")
        self.assertEqual(second.incidents, [])
        self.assertEqual(second.memory, self.original_memory)
        self.assertEqual(self.public_items()[0]["id"], original_id)
        self.assertTrue(self.public_items()[0]["clip"])
        self.now += dt.timedelta(minutes=2)
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2, "A saved successful video must not repeat")

    def test_failed_first_delivery_is_retryable_and_never_becomes_sent(self):
        self.delivery.return_value = None
        state = self.cycle()
        self.assertFalse(state.sent)
        self.delivery.return_value = 123
        self.now += dt.timedelta(minutes=2)
        retried = self.cycle()
        self.assertEqual(self.delivery.call_count, 2)
        self.assertIsNone(self.delivery.call_args.kwargs["reply_to"])
        self.assertTrue(retried.sent)
        self.now += dt.timedelta(minutes=2)
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2)

    def test_dry_run_preserves_first_real_notification_eligibility(self):
        state = self.cycle(send=False)
        self.assertFalse(state.sent)
        records = list(state.state["early_night"].values())
        self.assertEqual(len(records), 1)
        self.assertNotIn("sent_at", records[0])
        self.assertNotIn("tg_msg", records[0])
        self.assertFalse(self.delivery.call_args.args[4])
        self.now += dt.timedelta(minutes=2)
        result = self.cycle()
        self.assertTrue(result.sent)
        self.assertEqual(self.delivery.call_count, 2)
        self.assertTrue(self.delivery.call_args.args[4])
        self.assertIsNone(self.delivery.call_args.kwargs["reply_to"])
        sent = next(iter(result.state["early_night"].values()))
        self.assertEqual(sent["tg_msg"], 123)
        self.now += dt.timedelta(minutes=2)
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2)

    def test_missing_credentials_preserve_first_real_notification_eligibility(self):
        self.cfg["telegram"].update(bot_token="", chat_id="")
        state = self.cycle()
        self.assertFalse(state.sent)
        record = next(iter(state.state["early_night"].values()))
        self.assertNotIn("sent_at", record)
        self.assertNotIn("tg_msg", record)
        self.cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
        self.now += dt.timedelta(minutes=2)
        state = self.cycle()
        self.assertTrue(state.sent)
        self.assertEqual(self.delivery.call_count, 2)
        self.assertIsNone(self.delivery.call_args.kwargs["reply_to"])
        self.assertEqual(next(iter(state.state["early_night"].values()))["tg_msg"], 123)

    def test_exact_origin_promotion_replies_with_ready_video_without_new_fire_alert(self):
        self.cycle()
        early_id = self.public_items()[0]["id"]
        origin = dict(self.candidate, family="mtg", kind="geo", frp=None)
        incident = new_incident(origin, "new")
        add_to_incident(incident, origin)
        measured = dict(origin, src="mtg_frp", t="2026-11-13T19:00:00Z", frp=13.4)
        add_to_incident(incident, measured)
        incident.update(place="sea", where="في البحر قرب مضيق هرمز", country="عُمان",
                        facility=None, known_days=0, area_days=0,
                        _pos=[round(incident["lat"], 3), round(incident["lon"], 3), "offline-map"])
        finalize(incident, self.cfg)
        self.assertEqual(incident["verdict"], "مرجّح")
        for key, suffix in (("clip", "mp4"), ("snap", "jpg")):
            name = f"clips/real-measurement.{suffix}"
            target = self.cfg["_site_dir"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"finished measured media")
            incident[key] = name
        incident.update(clip_max_frp=13.4, clip_peak_time=measured["t"],
                        clip_style=engine.CLIP_STYLE, clip_updated_at=iso(self.now))
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])
        self.now += dt.timedelta(minutes=2)
        result = self.cycle()
        self.assertEqual(self.delivery.call_count, 2)
        call = self.delivery.call_args
        self.assertEqual(call.kwargs["reply_to"], 123)
        self.assertEqual(call.args[3], self.cfg["_site_dir"] / "clips/real-measurement.mp4")
        self.assertIn("تحديث", call.args[2])
        self.assertNotIn("حريق جديد", call.args[2])
        promoted = result.incidents[0]
        self.assertEqual(promoted["tg_msg"], 123)
        self.assertEqual(promoted["alerted_verdict"], "مرجّح")
        self.assertIn(early_id, promoted["early_ids"])
        self.assertEqual([item["id"] for item in self.public_items()], [incident["id"]])
        self.now += dt.timedelta(minutes=2)
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2)

    def test_two_early_origins_joined_by_normal_grouping_do_not_generate_third_alert(self):
        second = dict(self.candidate, lat=26.38)
        raw = load_json(self.cfg["_state_dir"] / "state.json", {})
        raw["sea_candidates"].append(second)
        save_json(self.cfg["_state_dir"] / "state.json", raw)
        self.delivery.side_effect = [123, 124]
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2)
        early_ids = {item["id"] for item in self.public_items()}
        self.assertEqual(len(early_ids), 2)
        origins = [dict(row, family="mtg", kind="geo", frp=None)
                   for row in (self.candidate, second)]
        incident = new_incident(origins[0], "new")
        for row in origins:
            add_to_incident(incident, row)
        # A later official observation gives this ordinary record enough evidence
        # for a normal alert, but does not resolve which early thread owns it.
        add_to_incident(incident, dict(origins[0], src="mtg_frp", t="2026-11-13T19:00:00Z", frp=13.4))
        incident.update(place="sea", where="في البحر قرب مضيق هرمز", country="عُمان",
                        facility=None, known_days=0, area_days=0,
                        _pos=[round(incident["lat"], 3), round(incident["lon"], 3), "offline-map"])
        finalize(incident, self.cfg)
        self.assertEqual(incident["verdict"], "مرجّح")
        self.assertEqual(incident["priority"], "high")
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])
        self.now += dt.timedelta(minutes=2)
        result = self.cycle()
        self.assertEqual(self.delivery.call_count, 2)
        joined = result.incidents[0]
        self.assertTrue(joined["early_promotion_ambiguous"])
        self.assertEqual(set(joined["early_ids"]), early_ids)
        self.assertNotIn("tg_msg", joined)
        self.assertIsNone(joined["alerted_verdict"])
        public = self.public_items()
        self.assertEqual(len(public), 1)
        self.assertTrue(public[0]["early_promotion_ambiguous"])
        self.now += dt.timedelta(minutes=2)
        self.cycle()
        self.assertEqual(self.delivery.call_count, 2, "Ambiguous promotion must not create a third alert")

    def test_early_job_is_processed_ahead_of_newer_routine_render(self):
        early = {"id": "early", "preliminary": True, "first": self.candidate["t"],
                 "last": self.candidate["t"], "lat": 26.3716, "lon": 56.2988,
                 "srcs": ["mtg_spot"], "max_frp": None}
        normal = dict(early, id="ordinary", preliminary=False, srcs=["mtg_frp"])
        for index, record in enumerate((early, normal)):
            mediaqueue.request_clip(self.cfg, record, self.now)
            folder = mediaqueue.queue_dir(self.cfg) / record["media_job"]
            os.utime(folder, (100 + index, 100 + index))
        processed = []
        def renderer(cfg, wms, state, inc, now, refresh=False):
            processed.append(inc["id"])
            return False
        session = MagicMock()
        session.__enter__.return_value = session
        with patch.object(mediaqueue, "make_session", return_value=session), \
                patch.object(engine, "add_clip", side_effect=renderer):
            self.assertTrue(mediaqueue.process_one(self.cfg, self.now))
        self.assertEqual(processed, ["early"])


class EarlyClipDispatchTests(TestCase):
    def test_preliminary_clip_uses_original_grid_renderer_without_latest_image_wait(self):
        now = dt.datetime(2026, 11, 13, 19, 5, tzinfo=UTC)
        inc = {"id": "early", "preliminary": True}
        cfg, wms, state = {}, Mock(), Mock()
        with patch("rasad.earlyclip.render", return_value=(Path("first.mp4"), Path("first.jpg"))) as render, \
                patch.object(engine, "utcnow", return_value=now), \
                patch.object(engine, "latest_time", side_effect=AssertionError("Do not wait for a future image")):
            result = engine.clip_for(cfg, wms, state, inc)
        self.assertEqual(result[0], Path("first.mp4"))
        render.assert_called_once_with(cfg, wms, inc, now)

    def test_invisible_preliminary_clip_is_not_reclassified_as_a_confirmed_incident(self):
        now = dt.datetime(2026, 11, 13, 19, 5, tzinfo=UTC)
        cfg = copy.deepcopy(engine.DEFAULTS)
        inc = {"id": "early", "preliminary": True, "srcs": ["mtg_spot"],
               "first": "2026-11-13T18:50:00Z", "last": "2026-11-13T18:50:00Z",
               "headline": "بقعة حرارية أولية", "verdict": "يحتاج تأكيداً"}
        with patch.object(engine, "clip_for", side_effect=NotVisible("No complete first frame")), \
                patch.object(engine, "finalize", side_effect=AssertionError("Never finalize a provisional spot")):
            self.assertFalse(engine.add_clip(cfg, Mock(), Mock(), inc, now))
        self.assertEqual(inc["headline"], "بقعة حرارية أولية")
        self.assertEqual(inc["verdict"], "يحتاج تأكيداً")
        self.assertNotIn("unseen_close", inc)
        self.assertNotIn("max_frp", inc)
        self.assertIn("الأولية", inc["clip_note"])
