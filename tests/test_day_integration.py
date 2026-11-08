"""Offline whole-cycle checks for experimental daylight candidates and alert gates."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory, add_to_incident, new_incident
from rasad.util import UTC, iso, load_json, save_json


class DayIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = dt.datetime(2026, 11, 8, 6, tzinfo=UTC)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=root / "state", _site_dir=root / "site",
                        _geodata_dir=root / "geo")
        self.cfg["regions"] = [{"id": "gulf", "name": "خليج عُمان",
                                "bbox": [55, 23, 60, 28]}]
        self.cfg["alerts"]["min_verdict"] = "يحتاج تأكيداً"
        self.cfg["seawatch"].update(enabled=True, look_back=False, alerts=True)
        self.cfg["seawatch"].setdefault("daytime", {}).update(enabled=True, alerts=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["firms"]["map_key"] = "offline-test-key"
        self.cfg["clip"]["enabled"] = False
        self.cfg["telegram"].update(bot_token="offline-test-token", chat_id="offline-test-chat")
        save_json(self.cfg["_state_dir"] / "state.json", {
            "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
            "sea_check": engine.SEA_CHECK, "hello_sent": iso(self.now),
            "bootstrapped": iso(self.now)})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        self.seed_memory = copy.deepcopy(memory.cells)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.spots = [self.detection(minute=10), self.detection(minute=20),
                      self.detection(minute=30)]
        self.polar = []
        self.deliver = Mock(return_value=789)
        self.session = Mock()
        for method in ("get", "post", "put"):
            getattr(self.session, method).side_effect = AssertionError("No network in these tests")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر في خليج عُمان", "عُمان")
        self.source = Mock(side_effect=AssertionError("Unexpected European source request"))
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now),
            make_session=Mock(return_value=self.session), WMS=Mock(),
            Geo=Mock(return_value=geo), Availability=Mock(), latest_time=self.source,
            detect_geo_layer=self.source, firms_fetch=Mock(side_effect=lambda *a, **kw:
                                                          copy.deepcopy(self.polar)),
            add_clip=Mock(return_value=False), deliver=self.deliver,
            tg_send=Mock(side_effect=AssertionError("Unexpected direct Telegram request")))
        patches.start()
        self.addCleanup(patches.stop)
        watch_patch = patch.object(engine.seawatch, "watch", side_effect=self.watch)
        watch_patch.start()
        self.addCleanup(watch_patch.stop)

    def detection(self, src="mtg_day_spot", minute=10, confidence=None, frp=None):
        family = {"VIIRS_NOAA21_NRT": "viirs", "mtg_frp": "mtg"}.get(src, "mtg")
        return {"src": src, "family": family, "kind": "polar" if family == "viirs" else "geo",
                "t": f"2026-11-08T05:{minute:02d}:00Z", "lat": 25.0, "lon": 57.0,
                "frp": frp, "conf": confidence, "night": False,
                "daytime": src == "mtg_day_spot", "experimental": src == "mtg_day_spot"}

    def watch(self, cfg, wms, state, now, *args):
        state["sea_day_status"] = {
            "enabled": True, "experimental": True, "checked": 1, "missing": 0,
            "waiting": 0, "candidates": len(self.spots), "spots": len(self.spots),
            "last_checked": self.spots[-1]["t"] if self.spots else None}
        return copy.deepcopy(self.spots)

    def cycle(self):
        result = engine.run_cycle(self.cfg, send=True)
        self.source.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
        self.assertEqual(len(result.incidents), 1)
        return result, result.incidents[0]

    def test_day_only_is_visible_but_cannot_alert_with_lowered_minimum_verdict(self):
        state, incident = self.cycle()

        self.deliver.assert_not_called()
        self.assertEqual(incident["verdict"], "يحتاج تأكيداً")
        self.assertIn("نهارية", incident["headline"])
        self.assertNotIn("حريق", incident["headline"])
        self.assertIsNone(incident["alerted_verdict"])
        self.assertEqual(incident["srcs"], ["mtg_day_spot"])
        self.assertIsNone(incident["max_frp"])
        published = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["verdict"], "يحتاج تأكيداً")
        status = load_json(self.cfg["_site_dir"] / "data" / "meta.json", {})["sources"]["seawatch_day"]
        self.assertTrue(status["experimental"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["checked"], 1)
        self.assertEqual(status["latest"], "2026-11-08T05:30:00Z")

    def test_day_only_never_trains_the_usual_heat_memory(self):
        state, _ = self.cycle()

        self.assertEqual(state.memory, self.seed_memory)
        self.assertNotIn(Memory.key(25, 57), state.memory)

    def test_experimental_history_rows_do_not_train_the_memory_either(self):
        self.spots[0]["_history"] = True

        state, incident = self.cycle()

        self.assertEqual(state.memory, self.seed_memory)
        self.assertEqual(incident["n"], 2)
        self.deliver.assert_not_called()

    def test_low_or_unknown_nasa_cannot_unlock_day_alert_even_at_high_power(self):
        for confidence in ("l", None, "unknown", 39):
            with self.subTest(confidence=confidence):
                # Each fixture uses a new NASA source/time key and a fresh candidate incident.
                save_json(self.cfg["_state_dir"] / "incidents.json", [])
                state_path = self.cfg["_state_dir"] / "state.json"
                raw = load_json(state_path, {})
                raw["seen"] = {}
                save_json(state_path, raw)
                self.polar = [self.detection("VIIRS_NOAA21_NRT", minute=40,
                                             confidence=confidence, frp=250)]

                _, incident = self.cycle()

                self.deliver.assert_not_called()
                self.assertEqual(incident["verdict"], "يحتاج تأكيداً")
                self.assertIsNone(incident["alerted_verdict"])

    def test_nominal_nasa_corroboration_allows_one_alert(self):
        self.polar = [self.detection("VIIRS_NOAA21_NRT", minute=40, confidence="n", frp=2)]

        state, incident = self.cycle()

        self.deliver.assert_called_once()
        self.assertEqual(incident["verdict"], "مؤكد")
        self.assertEqual(incident["status"], "alerted")
        self.assertEqual(incident["tg_msg"], 789)
        self.assertEqual(incident["max_frp"], 2)
        self.assertEqual(state.memory[Memory.key(25, 57)], {"2026-11-08": 2})

    def test_official_geo_corroboration_allows_one_alert(self):
        self.spots.append(self.detection("mtg_frp", minute=40, frp=30))

        state, incident = self.cycle()

        self.deliver.assert_called_once()
        self.assertEqual(incident["verdict"], "مؤكد")
        self.assertEqual(incident["status"], "alerted")
        self.assertEqual(state.memory[Memory.key(25, 57)], {"2026-11-08": 30})

    def test_existing_night_finder_still_alerts(self):
        self.spots = [self.detection("mtg_spot", minute=m) for m in (10, 20, 30)]

        _, incident = self.cycle()

        self.deliver.assert_called_once()
        self.assertEqual(incident["verdict"], "مرجّح")
        self.assertEqual(incident["status"], "alerted")

    def test_day_and_low_nasa_do_not_block_existing_night_fire_intensity_update(self):
        self.cfg["alerts"]["min_verdict"] = "مرجّح"
        night = self.detection("mtg_spot")
        night.update(t="2026-11-08T02:00:00Z", night=True)
        incident = new_incident(night, "new")
        add_to_incident(incident, night)
        add_to_incident(incident, dict(night, t="2026-11-08T02:10:00Z"))
        incident.update(
            status="alerted", verdict="مرجّح", alerted_verdict="مرجّح", tg_msg=123,
            alerted_at="2026-11-08T04:00:00Z", alert_updated_at="2026-11-08T05:00:00Z",
            known_days=0, area_days=0, max_frp=2, alerted_max_frp=2,
            frp_series=[["2026-11-08T02:10:00Z", 2, "viirs"]])
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])
        self.polar = [self.detection("VIIRS_NOAA21_NRT", minute=40, confidence="l", frp=13)]

        _, incident = self.cycle()

        self.deliver.assert_called_once()
        self.assertEqual(self.deliver.call_args.kwargs["reply_to"], 123)
        message = self.deliver.call_args.args[2]
        self.assertIn("ارتفاع شدة الحريق", message)
        self.assertNotIn("مؤكد الآن", message)
        self.assertEqual(incident["verdict"], "مرجّح")
        self.assertEqual(incident["alerted_verdict"], "مرجّح")
        self.assertEqual(incident["alerted_max_frp"], 13)
        self.assertEqual(len(incident["own_night_slots"]), 2)
        self.cycle()
        self.deliver.assert_called_once()

    def test_pending_video_retry_cannot_bypass_day_confirmation_gate(self):
        _, incident = self.cycle()
        incident.update(status="alerted", alerted_verdict="يحتاج تأكيداً",
                        alerted_max_frp=None, alert_updated_at=iso(self.now),
                        clip_video_pending=True, tg_msg=123)
        save_json(self.cfg["_state_dir"] / "incidents.json", [incident])
        self.cfg["clip"]["enabled"] = True

        def render(cfg, wms, state, current, now, refresh=False):
            name = "clips/day-review.mp4"
            path = cfg["_site_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"offline media fixture")
            current["clip"] = name
            return True

        engine.add_clip.side_effect = render

        _, incident = self.cycle()

        self.deliver.assert_not_called()
        self.assertTrue(incident["clip_video_pending"])
        self.assertEqual(incident["verdict"], "يحتاج تأكيداً")


if __name__ == "__main__":
    unittest.main()
