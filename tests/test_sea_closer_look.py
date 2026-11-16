"""What Rasad's own sea finder finds alone is posted only once its closer look shows it.

16 Nov 2026, off Taqah (Oman): a faint line in three pictures went out as a likely fire, and
79 seconds later the tool's own closer look did not show it (docs/reviews/taqah_sea_speck_20261116.md).
"""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from rasad import engine, mediaqueue
from rasad.brain import Memory
from rasad.clip import NotVisible
from rasad.util import UTC, iso, load_json, save_json

REAL_ADD_CLIP = engine.add_clip


class SeaCloserLook(unittest.TestCase):
    SLOTS = ["2026-11-15T20:50:00Z", "2026-11-15T21:10:00Z"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = dt.datetime(2026, 11, 15, 23, 5, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site",
                        _geodata_dir=self.root / "geo", _background_media=True)
        self.cfg["regions"] = [{"id": "oman", "name": "عُمان", "bbox": [52, 16, 57, 18]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["telegram"].update(bot_token="offline-token", chat_id="offline-chat")
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": iso(self.now)})
        memory = Memory({})
        memory.record(30, 48, "2026-11-14", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.save_incident(self.speck())
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر، على بعد 7.2 كم جنوب غرب طاقة (عُمان)", "عُمان")
        self.deliver = Mock(return_value=1219)
        self.source = Mock(side_effect=AssertionError("No source request expected"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now),
            make_session=Mock(return_value=self.session), WMS=Mock(), Geo=Mock(return_value=geo),
            Availability=Mock(), latest_time=self.source, detect_geo_layer=self.source,
            firms_fetch=self.source, deliver=self.deliver,
            tg_send=Mock(side_effect=AssertionError("No direct Telegram request")))
        patches.start()
        self.addCleanup(patches.stop)

    def speck(self, **changes):
        inc = {"id": "speck", "kind": "new", "first": self.SLOTS[0], "last": self.SLOTS[-1],
               "lat": 16.9876, "lon": 54.3582, "best": "geo", "families": ["mtg"], "srcs": ["mtg_spot"],
               "geo_slots": list(self.SLOTS), "own_night_slots": list(self.SLOTS), "flame_glow": True,
               "observations": [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t,
                                 "lat": 16.9876, "lon": 54.3582} for t in self.SLOTS],
               "polar": [], "max_frp": None, "n": 2, "status": "new", "alerted_verdict": None,
               "known_days": 0, "area_days": 0, "place": "sea", "where": "في البحر", "facility": None,
               "country": "عُمان", "priority": "high"}   # no description yet: its second picture just came
        inc.update(changes)
        return inc

    def incident(self):
        return load_json(self.cfg["_state_dir"] / "incidents.json", [])[0]

    def save_incident(self, incident):
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])

    def check(self, minutes=0):
        self.now += dt.timedelta(minutes=minutes)
        # the check itself never renders while the media worker exists
        with patch.object(engine, "add_clip", side_effect=AssertionError("The check must not render")):
            engine.run_cycle(self.cfg, send=True)
        self.session.get.assert_not_called()
        return self.incident()

    def closer_look(self, shows):
        """The media worker takes the job: the real add_clip around a stand-in for the pictures."""
        def pictures(cfg, wms, state, incident, **kw):
            if not shows:
                raise NotVisible("the fire does not show in these pictures")
            for suffix in ("mp4", "jpg"):
                target = cfg["_site_dir"] / f"clips/speck.{suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"the closer look")
            return cfg["_site_dir"] / "clips/speck.mp4", cfg["_site_dir"] / "clips/speck.jpg"
        session = MagicMock()
        session.__enter__.return_value = session
        with patch.object(mediaqueue, "make_session", return_value=session), \
                patch.object(engine, "add_clip", side_effect=REAL_ADD_CLIP), \
                patch.object(engine, "clip_for", side_effect=pictures):
            self.assertTrue(mediaqueue.process_one(self.cfg, now=self.now))

    def test_the_alert_waits_for_the_closer_look(self):
        inc = self.check()
        self.deliver.assert_not_called()
        self.assertEqual((inc["verdict"], inc["priority"], inc["alerted_verdict"]), ("مرجّح", "high", None))
        self.assertTrue(inc.get("media_job"), "the closer look was asked for")
        self.assertEqual(inc["closer_look_asked"], iso(self.now))
        self.check(2)
        self.deliver.assert_not_called()

    def test_not_shown_by_the_closer_look_it_stays_in_the_app(self):
        self.check()
        self.closer_look(shows=False)
        inc = self.check(2)
        self.assertEqual((inc["verdict"], inc["priority"], inc["status"]), ("يحتاج تأكيداً", "low", "new"))
        self.assertTrue(inc["unseen_close"])
        self.assertIn("لا يظهر بوضوح عند التدقيق", inc["clip_note"])
        for later in (5, 20, 60):                  # ... and never goes out by itself afterwards
            self.check(later)
        self.deliver.assert_not_called()
        self.assertIsNone(self.incident()["alerted_verdict"])

    def test_shown_by_the_closer_look_it_goes_out_with_its_video(self):
        self.check()
        self.closer_look(shows=True)
        inc = self.check(2)
        self.deliver.assert_called_once()
        post = self.deliver.call_args
        self.assertIn("حريق في البحر قرب عُمان", post.args[2])
        self.assertEqual(post.args[3], self.cfg["_site_dir"] / "clips/speck.mp4")
        self.assertNotIn("يُرسل الفيديو هنا", post.args[2])
        self.assertEqual((inc["status"], inc["alerted_verdict"], inc["tg_msg"]), ("alerted", "مرجّح", 1219))
        self.assertFalse(inc["clip_video_pending"])
        self.check(3)
        self.deliver.assert_called_once()

    def test_without_an_answer_in_time_the_alert_goes_out_as_before(self):
        self.check()
        self.check(10)
        self.deliver.assert_not_called()
        inc = self.check(6)                        # sixteen minutes and the worker has not answered
        self.deliver.assert_called_once()
        self.assertIsNone(self.deliver.call_args.args[3])
        self.assertIn("🎞 يُرسل الفيديو هنا إن ظهر الحدث بوضوح في الصور.", self.deliver.call_args.args[2])
        self.assertTrue(inc["clip_video_pending"])

    def test_seen_by_another_source_too_it_does_not_wait(self):
        self.save_incident(self.speck(srcs=["mtg_spot", "mtg_frp"], max_frp=6.0,
                                      frp_series=[[self.SLOTS[-1], 6.0, "mtg"]]))
        self.check()
        self.deliver.assert_called_once()

    def test_the_owner_can_switch_the_wait_off(self):
        self.cfg["seawatch"]["closer_look_first"] = False
        self.check()
        self.deliver.assert_called_once()

    def test_in_one_process_the_look_is_taken_on_the_spot(self):
        self.cfg["_background_media"] = False
        with patch.object(engine, "clip_for", side_effect=NotVisible("the fire does not show in these pictures")):
            engine.run_cycle(self.cfg, send=True)
        self.deliver.assert_not_called()
        self.assertEqual(self.incident()["verdict"], "يحتاج تأكيداً")


if __name__ == "__main__":
    unittest.main()
