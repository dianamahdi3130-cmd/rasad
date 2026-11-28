"""One ship, one thread (the owner's choice of 28 Nov 2026): a fire at sea whose heat belongs to a fire
already posted goes out as a reply under that post once chains.thread_post is switched on; until then
the decision is only recorded. The case: Khasab, 28 Nov 2026, 02:50 Baghdad time, two spots of one
picture 3.0 km apart on the same latitude, the second posted at 04:46 as a new fire."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine, reading
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json


def spots(rows):
    return [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": la, "lon": lo} for t, la, lo in rows]


class OneThread(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = dt.datetime(2026, 11, 8, 12, 2, tzinfo=UTC)
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
        ship_rows = [("2026-11-08T11:30:00Z", 26.30, 56.30), ("2026-11-08T11:40:00Z", 26.30, 56.30),
                     ("2026-11-08T11:50:00Z", 26.30, 56.30)]
        ship = {"id": "ship", "kind": "new", "first": "2026-11-08T11:30:00Z", "last": "2026-11-08T11:50:00Z",
                "lat": 26.30, "lon": 56.30, "best": "geo", "families": ["mtg"], "srcs": ["mtg_spot"],
                "geo_slots": [r[0] for r in ship_rows], "own_night_slots": [r[0] for r in ship_rows],
                "observations": spots(ship_rows), "polar": [], "max_frp": None, "n": 3,
                "status": "alerted", "verdict": "مؤكد", "alerted_verdict": "مؤكد",
                "alerted_at": "2026-11-08T11:42:00Z", "alert_updated_at": "2026-11-08T11:42:00Z", "tg_msg": 123,
                "known_days": 0, "area_days": 0, "place": "sea", "facility": None, "country": "عُمان",
                "priority": "high", "clip": "clips/ship.mp4", "snap": "clips/ship.png",
                "clip_style": engine.CLIP_STYLE, "_pos": [26.3, 56.3, "offline-map"]}
        east_rows = [("2026-11-08T11:50:00Z", 26.30, 56.335), ("2026-11-08T12:00:00Z", 26.30, 56.335)]
        east = {"id": "east", "kind": "new", "first": "2026-11-08T11:50:00Z", "last": "2026-11-08T12:00:00Z",
                "lat": 26.30, "lon": 56.335, "best": "geo", "families": ["mtg"], "srcs": ["mtg_spot"],
                "geo_slots": [r[0] for r in east_rows], "own_night_slots": [r[0] for r in east_rows],
                "observations": spots(east_rows), "polar": [], "max_frp": None, "n": 2,
                "status": "new", "alerted_verdict": None, "known_days": 0, "area_days": 0}
        for name in ("clips/ship.mp4", "clips/ship.png"):
            path = self.cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"clip")
        save_json(self.cfg["_state_dir"] / "incidents.json", [ship, east])
        self.deliver = Mock(return_value=456)
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network in these tests")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر قرب مضيق هرمز", "عُمان")
        source = Mock(side_effect=AssertionError("No source requests in these tests"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now), make_session=Mock(return_value=self.session),
            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(), latest_time=source,
            detect_geo_layer=source, firms_fetch=source, add_clip=Mock(side_effect=self.render),
            deliver=self.deliver, tg_send=Mock(side_effect=AssertionError("No direct Telegram requests")))
        patches.start()
        self.addCleanup(patches.stop)

    def render(self, cfg, wms, state, incident, now, refresh=False):
        for field, suffix in (("clip", "mp4"), ("snap", "png")):
            name = f"clips/{incident['id']}.{suffix}"
            path = cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"clip")
            incident[field] = name
        incident["clip_style"] = engine.CLIP_STYLE
        incident["clip_updated_at"] = iso(now)
        return True

    def first_alert(self):
        """The one message the check sends: the east spot's first alert."""
        engine.run_cycle(self.cfg, send=True)
        calls = [c for c in self.deliver.call_args_list]
        self.assertEqual(len(calls), 1, [c.args[2][:60] for c in calls])
        east = next(i for i in load_json(self.cfg["_state_dir"] / "incidents.json", []) if i["id"] == "east")
        return calls[0], east

    def test_silent_the_alert_is_a_post_and_the_decision_is_recorded(self):
        call, east = self.first_alert()
        self.assertIsNone(call.kwargs.get("reply_to"))
        self.assertNotIn("ما زالت النار مشتعلة", call.args[2])
        self.assertEqual((east["thread"]["as"], east["thread"]["link"], east["thread"]["under"]), ("reply", "same", "ship"))
        self.assertNotIn("thread_of", east)
        log = load_json(self.cfg["_state_dir"] / "state.json", {}).get("thread_log")
        self.assertEqual((log[-1]["id"], log[-1]["posted_as"]), ("east", "post"))

    def test_switched_on_it_is_a_reply_under_the_first_post(self):
        self.cfg["chains"] = {"thread_post": True}
        call, east = self.first_alert()
        self.assertEqual(call.kwargs.get("reply_to"), 123)
        self.assertTrue(call.args[2].startswith("🔥 <b>ما زالت النار مشتعلة</b>"))
        self.assertNotIn("قبل وبعد", call.args[2])           # the picture line belongs to a first post
        self.assertEqual((east["status"], east["tg_msg"], east["thread_of"]), ("alerted", 456, "ship"))
        log = load_json(self.cfg["_state_dir"] / "state.json", {}).get("thread_log")
        self.assertEqual(log[-1]["posted_as"], "reply")

    def test_a_reply_has_no_reading_of_its_own_and_its_thread_waits_for_it(self):
        P = {"reading": {"enabled": True, "post": True}}
        cfg = dict(self.cfg, **P)
        now = dt.datetime(2026, 11, 8, 13, 30, tzinfo=UTC)
        root = {"id": "ship", "status": "alerted", "tg_msg": 123, "last": "2026-11-08T11:50:00Z"}
        reply = {"id": "east", "status": "alerted", "tg_msg": 456, "last": "2026-11-08T13:00:00Z", "thread_of": "ship"}
        self.assertFalse(reading.due(reply, cfg, now, [root, reply]))
        self.assertFalse(reading.due(root, cfg, now, [root, reply]))   # its heat was seen 30 minutes ago
        later = dt.datetime(2026, 11, 8, 14, 30, tzinfo=UTC)
        self.assertTrue(reading.due(root, cfg, later, [root, reply]))


if __name__ == "__main__":
    unittest.main()
