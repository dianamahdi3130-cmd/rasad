"""The confidence number and its reasons, the record rule for plants' flares, the long-term archive,
and the lines they add to a channel post."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import archive, assess, brain, earlyalerts, engine
from rasad.brain import Memory, add_to_incident, alert_message, finalize, new_incident
from rasad.util import UTC, iso, save_json


def config():
    cfg = copy.deepcopy(engine.DEFAULTS)
    cfg["app_url"] = "https://app.example/"
    return cfg


def det(src, family, t, kind="geo", frp=None, lat=26.02, lon=55.90, **extra):
    return dict({"src": src, "family": family, "kind": kind, "t": t, "lat": lat, "lon": lon, "frp": frp}, **extra)


def incident(dets, kind="new", place="sea", facility=None, **extra):
    inc = new_incident(dets[0], kind)
    for d in dets:
        add_to_incident(inc, d)
    inc.update(place=place, facility=facility, where="في البحر قرب مضيق هرمز", country="", regions=["hormuz"],
               region_name="مضيق هرمز", known_days=0, area_days=0, typical_frp=None)
    inc.update(extra)
    return finalize(inc, config())


class Confidence(unittest.TestCase):
    def test_single_sighting_stays_low_and_says_why(self):
        inc = incident([det("mtg_frp", "mtg", "2026-11-14T16:10:00Z", frp=12.0)], place="land")
        self.assertEqual(inc["verdict"], "يحتاج تأكيداً")
        self.assertEqual(inc["category"], "fire")
        self.assertLess(inc["confidence"], 50)
        self.assertEqual(inc["confidence_level"], "منخفضة")
        self.assertTrue(any("رمز حريق في صورة واحدة" in e["text"] for e in inc["evidence"]))

    def test_two_satellite_families_are_confirmed_and_high(self):
        inc = incident([det("mtg_frp", "mtg", "2026-11-14T16:10:00Z", frp=40.0),
                        det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T16:40:00Z", "polar", 35.0, conf="h", night=True)])
        self.assertEqual(inc["verdict"], "مؤكد")
        self.assertGreaterEqual(inc["confidence"], 75)
        self.assertEqual(inc["confidence_level"], "عالية")
        self.assertIn("اتفقت عائلتان", inc["agreement"])
        self.assertEqual([s["key"] for s in inc["sources"]], ["mtg_frp", "VIIRS_NOAA20_NRT"])
        self.assertEqual(assess.timeline_of(inc), [["2026-11-14T16:10:00Z", "mtg_frp", 40.0],
                                                   ["2026-11-14T16:40:00Z", "VIIRS_NOAA20_NRT", 35.0]])

    def test_number_never_contradicts_the_verdict(self):
        # the sea finder alone is at most likely, whatever the evidence adds up to
        spots = [det("mtg_spot", "mtg", f"2026-11-14T{hh}:00Z", flame=True) for hh in ("15:40", "15:50", "16:00", "16:10")]
        inc = incident(spots)
        self.assertEqual(inc["verdict"], "مرجّح")
        self.assertTrue(50 <= inc["confidence"] <= 74, inc["confidence"])
        self.assertEqual(inc["confidence_level"], "متوسطة")
        self.assertTrue(inc["flame_glow"])
        self.assertTrue(any("وهج لهب" in e["text"] for e in inc["evidence"]))

    def test_what_speaks_against_it_is_listed(self):
        inc = incident([det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T22:33:00Z", "polar", 4.0, conf="n", night=True)],
                       facility={"label": "منصة نفطية", "code": "platform", "km": 1.0, "nearest_km": 1.0},
                       known_days=1)
        against = [e["text"] for e in inc["evidence"] if e["s"] == "-"]
        self.assertTrue(any("منصة نفطية" in text for text in against))
        self.assertTrue(any("يوم واحد" in text for text in against))

    def test_a_plants_surge_is_its_own_kind_and_claims_no_cause(self):
        inc = incident([det("mtg_frp", "mtg", f"2026-11-15T07:{mm}:00Z", frp=frp, lat=28.99, lon=48.0)
                        for mm, frp in (("00", 46.0), ("10", 87.6), ("20", 110.0))],
                       kind="unusual", place="land",
                       facility={"label": "منشأة نفطية في منطقة الأحمدي", "code": "oil", "km": 0.9},
                       known_days=18, typical_frp=2.3, site_record=52.0)
        self.assertEqual(inc["category"], "industrial")
        self.assertEqual(inc["category_text"], "نشاط حراري في منشأة")
        texts = [e["text"] for e in inc["evidence"]]
        self.assertTrue(any("أعلى ما سُجّل هنا قبلها خلال شهر 52 MW" in text for text in texts))
        self.assertTrue(any("لا تفرّق بينهما" in text for text in texts))
        self.assertEqual(inc["severity"], "مرتفع")
        message = alert_message(inc, config())
        self.assertIn("🏭 شعلة هذا الموقع معتادة، وشدتها الآن أعلى مما سُجّل فيه خلال شهر (السابق 52 MW). "
                      "قد يكون حرق غاز تشغيلياً أو حريقاً؛ صور الأقمار لا تفرّق بينهما.", message)

    def test_severity_follows_place_and_strength(self):
        cfg = config()
        sea = incident([det("mtg_spot", "mtg", "2026-11-14T15:40:00Z"), det("mtg_spot", "mtg", "2026-11-14T15:50:00Z")])
        self.assertEqual(sea["severity"], "مرتفع")            # a likely fire at sea, strength unknown
        faint = incident([det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T22:33:00Z", "polar", 2.0, conf="l")],
                         place="land")
        self.assertEqual(faint["severity"], "منخفض")
        big = dict(sea, max_frp=250.0)
        self.assertEqual(assess.severity_of(big, cfg), "حرج")

    def test_first_unconfirmed_speck_is_a_candidate_with_low_confidence(self):
        item = {"id": "candidate-20261114-1540_26.0197_55.9190", "kind": "thermal_candidate", "preliminary": True,
                "first": "2026-11-14T15:40:00Z", "last": "2026-11-14T15:40:00Z", "lat": 26.0197, "lon": 55.919,
                "place": "sea", "facility": None, "verdict": "يحتاج تأكيداً", "priority": "high", "max_frp": None}
        assess.assess(item, config())
        self.assertEqual((item["category"], item["category_text"]), ("candidate", "بقعة حرارية أولية"))
        self.assertEqual((item["confidence"], item["confidence_level"]), (30, "منخفضة"))
        self.assertEqual(item["severity"], "متوسط")
        self.assertEqual([s["key"] for s in item["sources"]], ["mtg_spot"])
        self.assertTrue(any("ننتظر صورة ثانية" in e["text"] for e in item["evidence"]))

    def test_arabic_counts(self):
        self.assertEqual(assess._count(1, "صورة واحدة", "صورتين", "صور", "صورة"), "صورة واحدة")
        self.assertEqual(assess._count(2, "صورة واحدة", "صورتين", "صور", "صورة"), "صورتين")
        self.assertEqual(assess._count(5, "صورة واحدة", "صورتين", "صور", "صورة"), "5 صور")
        self.assertEqual(assess._count(25, "صورة واحدة", "صورتين", "صور", "صورة"), "25 صورة")


class OpenSea(unittest.TestCase):
    """NASA alone, at night, in open water."""

    def sea(self, *pixels):
        return incident([det("VIIRS_NOAA20_NRT", "viirs", "2026-11-11T22:33:00Z", "polar", frp, conf="n",
                             night=True, lat=28.45 + k * 0.004, lon=48.91) for k, frp in enumerate(pixels)])

    def test_one_faint_pixel_waits_for_a_second_look(self):
        # 0.57 MW off Khafji on 11 November 2026: a platform's small flare, posted as a sea fire
        inc = self.sea(0.57)
        self.assertEqual(inc["verdict"], "يحتاج تأكيداً")

    def test_a_pixel_of_some_strength_or_two_pixels_are_likely(self):
        self.assertEqual(self.sea(5.2)["verdict"], "مرجّح")
        self.assertEqual(self.sea(0.9, 1.1)["verdict"], "مرجّح")


class AlertLines(unittest.TestCase):
    def test_first_post_carries_the_confidence_line(self):
        inc = incident([det("mtg_spot", "mtg", "2026-11-14T15:40:00Z", flame=True),
                        det("mtg_spot", "mtg", "2026-11-14T15:50:00Z", flame=True)])
        message = alert_message(inc, config())
        rows = message.split("\n")
        at = next(k for k, row in enumerate(rows) if row.startswith("🎯"))
        self.assertEqual(rows[at], f"🎯 <b>الثقة:</b> {inc['confidence']}% (متوسطة) · الخطورة: مرتفع")
        self.assertEqual(rows[at + 1], "🔎 باحث رصد وجد بقعة حرارية ثابتة ليلاً في صورتين متتاليتين")
        self.assertNotIn("🏭 شعلة", message)                 # not a plant's surge
        self.assertNotIn("سبب", message)                     # never a cause

    def test_updates_stay_short(self):
        inc = incident([det("mtg_frp", "mtg", "2026-11-14T16:10:00Z", frp=40.0),
                        det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T16:40:00Z", "polar", 35.0, conf="h")])
        first = alert_message(inc, config())
        self.assertIn("✅ <b>مؤكد</b>", first)
        self.assertIn("🎯 <b>الثقة:</b>", first)
        self.assertNotIn("🔎", first)                        # the confirmed line already says why
        inc["alerted_verdict"] = "مرجّح"
        update = alert_message(inc, config(), update=True, threaded=True)
        self.assertNotIn("🎯", update)


class PlantRecord(unittest.TestCase):
    """A plant's flare is news only when it clearly beats its own record of the month."""
    NOW = dt.datetime(2026, 11, 15, 12, tzinfo=UTC)
    LAT, LON = 30.0, 48.0

    def cycle(self, measured, history):
        with tempfile.TemporaryDirectory() as folder:
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg.update(_state_dir=Path(folder) / "state", _site_dir=Path(folder) / "site",
                       _geodata_dir=Path(folder) / "geo")
            cfg["regions"] = [{"id": "test", "name": "العراق", "bbox": [47, 29, 49, 31]}]
            cfg["seawatch"]["enabled"] = False
            cfg["clip"]["enabled"] = False
            cfg["eumetview"]["fire_layers"] = cfg["eumetview"]["fire_layers"][:1]
            cfg["eumetview"]["lookback_slots"] = 1
            cfg["eumetview"]["catchup_hours"] = 0
            memory = Memory({})
            for ago, frp in enumerate(history, 1):
                memory.record(self.LAT, self.LON, (self.NOW - dt.timedelta(days=ago)).date().isoformat(), frp)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            save_json(cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES})
            observed = self.NOW - dt.timedelta(minutes=30)
            detection = {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": iso(observed),
                         "lat": self.LAT, "lon": self.LON, "frp": None,
                         "_fi": ["mtg_fd:frp", [47, 29, 49, 31], 10, 10, 5, 5, iso(observed)]}
            wms = Mock()
            wms.feature_info.return_value = {"frp": measured}
            geo = Mock(version="test")
            geo.facility_within.return_value = True
            geo.facility.return_value = {"label": "معمل غاز", "code": "gas", "km": 0.4}
            geo.place_kind.return_value = "land"
            geo.describe.return_value = ("السعودية", "السعودية")
            with patch.multiple(engine, utcnow=Mock(return_value=self.NOW), make_session=Mock(),
                                WMS=Mock(return_value=wms), Geo=Mock(return_value=geo),
                                Availability=Mock(), latest_time=Mock(return_value=observed),
                                detect_geo_layer=Mock(return_value=[detection])):
                state = engine.run_cycle(cfg, send=False)
            feed = json.loads((cfg["_site_dir"] / "data" / "incidents.json").read_text(encoding="utf-8"))
            return state, feed

    def test_a_surge_the_plant_has_shown_before_is_not_news(self):
        # Shedgum, 12-14 November 2026: 109 MW, then 94, then 80; three posts for one habit
        state, _ = self.cycle(94.0, [109.0, 8.0, 7.0, 6.0, 7.0, 8.0])
        self.assertEqual(state.incidents, [])
        self.assertEqual(state.memory[Memory.key(self.LAT, self.LON)]["2026-11-15"], 94.0)

    def test_a_surge_far_above_the_record_is(self):
        state, feed = self.cycle(240.0, [109.0, 8.0, 7.0, 6.0, 7.0, 8.0])
        self.assertEqual(len(state.incidents), 1)
        inc = state.incidents[0]
        self.assertEqual((inc["kind"], inc["site_record"], inc["category"]), ("unusual", 109.0, "industrial"))
        item = feed["items"][0]
        self.assertEqual((item["site_record"], item["category"], item["alerted"]), (109.0, "industrial", False))
        self.assertTrue(item["evidence"] and item["sources"] and item["timeline"])

    def test_first_ever_surge_needs_no_record(self):
        state, _ = self.cycle(60.0, [5.0, 6.0, 7.0])          # 12 times the usual, twice the record of 7
        self.assertEqual([inc["kind"] for inc in state.incidents], ["unusual"])

    def test_memory_peak_counts_today_and_the_neighbourhood(self):
        memory = Memory({})
        memory.record(30.0, 48.0, "2026-11-10", 40.0)
        memory.record(30.02, 48.0, "2026-11-15", 90.0)        # the next cell, earlier today
        memory.record(30.0, 48.0, "2026-10-01", 500.0)        # outside the window
        self.assertEqual(memory.peak(30.0, 48.0, "2026-11-15", 30, reach=1), 90.0)
        self.assertEqual(memory.peak(30.0, 48.0, "2026-11-15", 30, reach=0), 40.0)
        self.assertIsNone(memory.peak(10.0, 10.0, "2026-11-15", 30))


class Archive(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.cfg = dict(config(), _state_dir=root / "state", _site_dir=root / "site")
        self.now = dt.datetime(2026, 11, 15, 12, tzinfo=UTC)

    def read(self, name):
        return json.loads((self.cfg["_site_dir"] / "data" / "archive" / name).read_text(encoding="utf-8"))

    def test_events_worth_keeping_go_to_their_month_and_stay_after_the_engine_forgets_them(self):
        likely = incident([det("mtg_spot", "mtg", "2026-11-14T15:40:00Z"), det("mtg_spot", "mtg", "2026-11-14T15:50:00Z")])
        october = incident([det("mtg_frp", "mtg", f"2026-10-30T10:{mm}:00Z", frp=30.0) for mm in ("00", "10", "20")],
                           place="land")
        small = incident([det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T22:33:00Z", "polar", 2.0, conf="l")], place="land")
        self.assertFalse(archive.worth(small))
        self.assertEqual(archive.update(self.cfg, [likely, october, small], self.now), ["2026-10", "2026-11"])
        index = self.read("index.json")
        self.assertEqual([(m["month"], m["count"]) for m in index["months"]], [("2026-11", 1), ("2026-10", 1)])
        row = self.read("2026-11.json")["items"][0]
        self.assertEqual((row["id"], row["verdict"], row["category"], row["alerted"], row["pictures"]),
                         (likely["id"], "مرجّح", "fire", False, 2))
        self.assertNotIn("observations", row)
        # nothing changed: nothing rewritten; the event leaves the engine: it stays in the record
        self.assertEqual(archive.update(self.cfg, [likely, october], self.now), [])
        self.assertEqual(archive.update(self.cfg, [], self.now), [])
        self.assertEqual(len(self.read("2026-11.json")["items"]), 1)

    def test_a_live_event_is_replaced_by_its_newer_version(self):
        inc = incident([det("mtg_frp", "mtg", "2026-11-14T16:10:00Z", frp=40.0),
                        det("mtg_frp", "mtg", "2026-11-14T16:20:00Z", frp=45.0)], place="land")
        archive.update(self.cfg, [inc], self.now)
        add_to_incident(inc, det("VIIRS_NOAA20_NRT", "viirs", "2026-11-14T16:40:00Z", "polar", 35.0, conf="h"))
        finalize(inc, config())
        self.assertEqual(archive.update(self.cfg, [inc], self.now), ["2026-11"])
        rows = self.read("2026-11.json")["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "مؤكد")


class FirstPictureAlerts(unittest.TestCase):
    """A faint first speck waits for its second picture; an unmistakable one is told at once."""

    def sync(self, strong):
        cfg = config()
        cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.0, 25.0, 57.6, 27.3], "enabled": True}]
        cfg["seawatch"].update(enabled=True, alerts=True)
        cfg["seawatch"]["early_alerts"]["enabled"] = True
        now = dt.datetime(2026, 11, 14, 15, 55, tzinfo=UTC)
        state = Mock()
        state.state = {"sea_candidates": [{"src": "mtg_spot", "t": "2026-11-14T15:40:00Z", "lat": 26.02,
                                           "lon": 55.90, "rise": 4.6, "strong": strong, "flame": True}]}
        state.incidents = []
        with patch.object(earlyalerts.preliminary, "sun_elevation", return_value=-30.0):
            return earlyalerts.sync(cfg, state, now), state.state.get("early_night", {})

    def test_faint_first_picture_is_not_posted_alone(self):
        added, records = self.sync(strong=False)
        self.assertEqual((added, records), (0, {}))

    def test_unmistakable_first_picture_is(self):
        added, records = self.sync(strong=True)
        self.assertEqual(added, 1)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
