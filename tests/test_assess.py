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

    def test_the_finder_says_in_a_row_only_for_pictures_in_a_row(self):
        # the Taqah speck of 16 Nov 2026: seen at 20:50, 21:10 and 21:40 UTC, posted as «3 صور متتالية»
        inc = incident([det("mtg_spot", "mtg", f"2026-11-15T{hh}:00Z", flame=True, lat=16.9876, lon=54.3582)
                        for hh in ("20:50", "21:10", "21:40")])
        self.assertEqual(inc["reason"], "وجده الباحث الحراري في 3 صور للقمر الأوروبي، ولم يؤكده قمر آخر بعد")
        self.assertIn("🔎 باحث رصد وجد بقعة حرارية ثابتة ليلاً في 3 صور\n", alert_message(inc, config()))
        row = incident([det("mtg_spot", "mtg", f"2026-11-15T{hh}:00Z", flame=True, lat=16.9876, lon=54.3582)
                        for hh in ("20:50", "21:00", "21:10")])
        self.assertEqual(row["reason"], "وجده الباحث الحراري في 3 صور متتالية للقمر الأوروبي، ولم يؤكده قمر آخر بعد")
        two = incident([det("mtg_spot", "mtg", f"2026-11-15T{hh}:00Z", flame=True, lat=16.9876, lon=54.3582)
                        for hh in ("20:50", "21:10")])
        self.assertEqual(two["reason"], "وجده الباحث الحراري في صورتين للقمر الأوروبي، ولم يؤكده قمر آخر بعد")

    def test_a_sea_is_not_near_itself(self):
        # the live app read «حريق في البحر قرب الخليج الفارسي» for a fire in the Gulf itself
        self.assertEqual(brain.at_sea("حريق", "الخليج الفارسي"), "حريق في مياه الخليج الفارسي")
        self.assertEqual(brain.at_sea("بقعة حرارية أولية", "البحر الأحمر وباب المندب"),
                         "بقعة حرارية أولية في مياه البحر الأحمر وباب المندب")
        self.assertEqual(brain.at_sea("حريق", "الإمارات"), "حريق في البحر قرب الإمارات")
        self.assertEqual(brain.at_sea("حريق", "مضيق هرمز"), "حريق في البحر قرب مضيق هرمز")   # its region reaches beyond the strait
        self.assertEqual(brain.at_sea("حريق", None), "حريق في البحر")
        spots = [det("mtg_spot", "mtg", f"2026-11-14T{hh}:00Z", flame=True) for hh in ("15:40", "15:50")]
        self.assertEqual(incident(spots, region_name="الخليج الفارسي")["headline"], "حريق في مياه الخليج الفارسي")
        self.assertEqual(incident(spots)["headline"], "حريق في البحر قرب مضيق هرمز")

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


class NewHeat(unittest.TestCase):
    """A confirmed fire where there was no heat for a month, in the dry countries of the Gulf, is
    news even far from every mapped facility."""

    def fire(self, readings, **extra):
        # 15 Nov 2026, 28 km south of Judah (Saudi Arabia): an oil pumping station the map does not
        # hold. Seven pictures in a row from 17:40 UTC, up to 14 MW; confirmed and never posted.
        times = ["17:40", "17:50", "18:00", "18:10", "18:20", "18:30", "18:40"]
        dets = [det("mtg_frp", "mtg", f"2026-11-15T{hh}:00Z", frp=frp, lat=25.6093, lon=48.874)
                for hh, frp in zip(times, readings)]
        fields = dict(place="land", where="على بعد 28 كم جنوب جودة (السعودية)", country="السعودية",
                      regions=["gulf", "saudi"], region_name="الخليج الفارسي", known_days=0, area_days=0)
        fields.update(extra)
        return incident(dets, **fields)

    REAL = [7.7, 5.4, 13.9, 6.1, 7.7, 11.2, 7.0]

    def test_the_pumping_station_fire_of_15_november_is_news(self):
        inc = self.fire(self.REAL)
        self.assertEqual((inc["verdict"], inc["facility"], inc["priority"], inc["new_heat"]), ("مؤكد", None, "high", True))
        self.assertEqual(inc["headline"], "حريق جديد في السعودية")
        message = alert_message(inc, config())
        self.assertIn("🗺 لا منشأة مسجّلة في الخريطة هنا، ولم تُرصد حرارة في هذا المكان ولا حوله خلال شهر. "
                      "قد يكون موقعاً غير مرسوم (محطة ضخ، بئر، خط أنابيب).", message)
        self.assertIn("✅ <b>مؤكد</b> · ظهر في 7 صور متتالية خلال 60 دقيقة", message)
        self.assertNotIn("استهداف", message)                       # never a cause
        self.assertTrue(any("ولا منشأة مسجّلة في الخريطة" in e["text"] for e in inc["evidence"]))
        self.assertGreaterEqual(inc["confidence"], 75)

    def test_pictures_with_a_gap_are_not_called_an_unbroken_run(self):
        # the source stopped publishing between 18:50 and 21:30 UTC; the fire was seen again at 21:40
        times = ["17:40", "17:50", "18:00", "18:10", "18:20", "18:30", "18:40", "21:40"]
        dets = [det("mtg_frp", "mtg", f"2026-11-15T{hh}:00Z", frp=frp, lat=25.6093, lon=48.874)
                for hh, frp in zip(times, self.REAL + [6.0])]
        inc = incident(dets, place="land", country="السعودية", known_days=0, area_days=0)
        self.assertEqual((inc["verdict"], inc["reason"]), ("مؤكد", "ظهر في 8 صور خلال نحو 4 ساعات"))
        self.assertEqual(self.fire(self.REAL)["reason"], "ظهر في 7 صور متتالية خلال 60 دقيقة")
        short = incident([dets[0], dets[2], dets[6]], place="land", country="السعودية")
        self.assertEqual(short["reason"], "ظهر في 3 صور خلال 60 دقيقة", "one hour, but pictures were skipped")
        # the second European satellite takes a picture every 15 minutes: still one after the other
        iodc = [det("iodc_fire", "msg_iodc", f"2026-11-15T{hh}:00Z", lat=25.6093, lon=48.874)
                for hh in ("17:45", "18:00", "18:15")]
        self.assertEqual(incident(iodc, place="land")["reason"], "ظهر في 3 صور متتالية خلال 30 دقيقة")

    def test_it_is_posted_from_the_fourth_picture_or_at_once_when_strong(self):
        self.assertEqual(self.fire(self.REAL[:2])["priority"], "low", "two pictures: likely, not yet confirmed")
        self.assertEqual(self.fire(self.REAL[:3])["priority"], "low", "confirmed, but a short weak flicker so far")
        self.assertEqual(self.fire(self.REAL[:4])["priority"], "high")
        self.assertEqual(self.fire([7.7, 25.0, 13.9])["priority"], "high", "confirmed and strong: no need to wait")
        self.assertEqual(self.fire([2.0, 3.0, 4.0, 3.0, 2.5])["priority"], "low", "too weak to count")

    def test_only_where_nothing_burns_by_itself(self):
        self.assertEqual(self.fire(self.REAL, country="العراق")["priority"], "low", "farm country: fields burn every season")
        self.assertEqual(self.fire(self.REAL, known_days=1)["priority"], "low", "heat was seen here before")
        self.assertEqual(self.fire(self.REAL, area_days=1)["priority"], "low", "or around here")
        self.assertNotIn("new_heat", self.fire(self.REAL, country="العراق"))
        near = self.fire(self.REAL, facility={"label": "منشأة نفطية", "code": "oil", "km": 1.2})
        self.assertEqual(near["priority"], "high")                  # a mapped facility: posted as before...
        self.assertNotIn("new_heat", near)                          # ...without the "not on the map" note
        self.assertNotIn("لا منشأة مسجّلة", alert_message(near, config()))


class PlantRecord(unittest.TestCase):
    """A plant's flare is news only when it clearly beats its own habit: a level it reached on two
    different days of the month. One episode is posted once."""
    NOW = dt.datetime(2026, 11, 15, 12, tzinfo=UTC)
    LAT, LON = 30.0, 48.0

    def posted(self, days_ago, frp):
        """An «unusual heat» incident of this plant that went out to the channel."""
        when = iso(self.NOW - dt.timedelta(days=days_ago))
        inc = new_incident({"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": when, "lat": self.LAT,
                            "lon": self.LON, "frp": frp}, "unusual")
        add_to_incident(inc, {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": when, "lat": self.LAT,
                              "lon": self.LON, "frp": frp})
        inc.update(place="land", facility={"label": "معمل غاز", "code": "gas", "km": 0.4}, where="السعودية",
                   country="السعودية", regions=["test"], region_name="العراق", known_days=6, area_days=6,
                   typical_frp=7.5, _pos=[self.LAT, self.LON, "test"])
        finalize(inc, config())
        inc["alerted_verdict"], inc["alerted_at"] = "مرجّح", when
        return inc

    def cycle(self, measured, history, during=None, earlier_today=None, stored=()):
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
            if earlier_today is not None:               # what earlier checks of today already measured here
                memory.record(self.LAT, self.LON, self.NOW.date().isoformat(), earlier_today)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            save_json(cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES, "tidy": engine.TIDY})
            if stored:
                save_json(cfg["_state_dir"] / "incidents.json", list(stored))
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
                                detect_geo_layer=Mock(side_effect=lambda *a, **k: [during and during(cfg), [detection]][1])):
                state = engine.run_cycle(cfg, send=False)
                if during:
                    during(cfg)
            feed = json.loads((cfg["_site_dir"] / "data" / "incidents.json").read_text(encoding="utf-8"))
            return state, feed

    def test_the_check_says_when_it_reads_the_official_products(self):
        # the picture worker waits meanwhile (util.official_reads); afterwards the note is gone or ended
        from rasad import util
        left = []
        self.cycle(94.0, [109.0, 8.0], during=lambda cfg: left.append(util.official_reads_left(cfg)))
        self.assertGreater(left[0], 30, "while the fire symbols are read")
        self.assertLess(left[-1], 40, "after the check only the short note of the strength reads may remain")

    def test_a_level_the_plant_reaches_again_and_again_is_not_news(self):
        # a plant that touched 109, 94 and 80 MW on three days of the month (8 MW on most): 150 MW
        # is far above its usual, but within what it does again and again
        state, _ = self.cycle(150.0, [109.0, 94.0, 80.0] + [8.0] * 9)
        self.assertEqual(state.incidents, [])
        self.assertEqual(state.memory[Memory.key(self.LAT, self.LON)]["2026-11-15"], 150.0)

    def test_a_surge_far_above_the_habit_is(self):
        state, feed = self.cycle(240.0, [109.0, 94.0, 80.0] + [8.0] * 9)
        self.assertEqual(len(state.incidents), 1)
        inc = state.incidents[0]
        self.assertEqual((inc["kind"], inc["site_habit"], inc["site_record"], inc["category"]), ("unusual", 94.0, 109.0, "industrial"))
        item = feed["items"][0]
        self.assertEqual((item["site_habit"], item["site_record"], item["category"], item["alerted"]), (94.0, 109.0, "industrial", False))
        self.assertTrue(item["evidence"] and item["sources"] and item["timeline"])
        # the heat at a plant is certain; its number says how far outside the plant's habits it is
        self.assertEqual(item["confidence_label"], "الثقة بأنه خارج المعتاد")
        self.assertTrue(40 <= item["confidence"] <= 90)
        message = alert_message(inc, config())
        row = next(r for r in message.split("\n") if r.startswith("🎯"))
        self.assertTrue(row.startswith(f"🎯 <b>الثقة بأنه خارج المعتاد:</b> {inc['confidence']}% ("), row)
        self.assertIn("🏭 شعلة هذا الموقع معتادة، وشدتها الآن أعلى بكثير مما اعتاد بلوغه خلال شهر (نحو 94 MW).", message)

    def test_one_earlier_spike_is_not_a_habit(self):
        # the plant flared to 109 MW once (never posted) and sits at 8 MW otherwise: 94 MW is news
        state, _ = self.cycle(94.0, [109.0, 8.0, 7.0, 6.0, 7.0, 8.0])
        self.assertEqual([(inc["kind"], inc["site_habit"], inc["site_record"]) for inc in state.incidents], [("unusual", 8.0, 109.0)])

    def test_one_episode_is_posted_once(self):
        # Shedgum, 12-14 November 2026: 109 MW was posted, then 94 and 80 MW were posted again
        yesterday = self.posted(1, 109.0)
        state, _ = self.cycle(94.0, [109.0, 8.0, 7.0, 6.0, 7.0, 8.0], stored=[yesterday])
        self.assertEqual([inc["id"] for inc in state.incidents], [yesterday["id"]], "no second post for the same episode")
        # ...unless it now burns half as hard again as what was posted
        state, _ = self.cycle(170.0, [109.0, 8.0, 7.0, 6.0, 7.0, 8.0], stored=[self.posted(1, 109.0)])
        self.assertEqual(len(state.incidents), 2)
        # and an episode posted long ago does not hold a new one back
        state, _ = self.cycle(94.0, [8.0, 7.0, 6.0, 7.0, 8.0, 109.0], stored=[self.posted(6, 109.0)])
        self.assertEqual(len(state.incidents), 2)

    def test_a_plant_climbing_day_after_day_does_not_silence_itself(self):
        # Ahmadi, 11-15 November 2026: usually 2 MW, then 49, 66, 85 MW on three days (none posted
        # under the first rule, each a little above the last) and 110-118 MW on the fourth: no word
        state, _ = self.cycle(110.0, [85.0, 52.0, 47.0, 37.0] + [2.0] * 10)
        self.assertEqual([(inc["kind"], inc["site_habit"], inc["site_record"]) for inc in state.incidents], [("unusual", 52.0, 85.0)])

    def test_a_fire_growing_through_the_day_is_not_measured_against_itself(self):
        # earlier checks of today read 30 MW here (not yet twice the plant's habit of 18); now it is 45
        state, _ = self.cycle(45.0, [20.0, 18.0] + [3.0] * 8, earlier_today=30.0)
        self.assertEqual([inc["kind"] for inc in state.incidents], ["unusual"])
        self.assertEqual(Memory({}).habit(0, 0, "2026-11-15", 30), (None, None))

    def test_first_ever_surge_needs_no_record(self):
        state, _ = self.cycle(60.0, [5.0, 6.0, 7.0])          # 12 times the usual, far above anything seen here
        self.assertEqual([inc["kind"] for inc in state.incidents], ["unusual"])

    def test_the_habit_leaves_today_out_and_looks_around(self):
        memory = Memory({})
        memory.record(30.0, 48.0, "2026-11-10", 40.0)
        memory.record(30.0, 48.0, "2026-11-12", 55.0)
        memory.record(30.02, 48.0, "2026-11-13", 70.0)        # the next cell
        memory.record(30.0, 48.0, "2026-11-15", 300.0)        # today: not part of the habit
        memory.record(30.0, 48.0, "2026-10-01", 500.0)        # outside the window
        self.assertEqual(memory.habit(30.0, 48.0, "2026-11-15", 30, reach=1), (55.0, 70.0))
        self.assertEqual(memory.habit(30.0, 48.0, "2026-11-15", 30, reach=0), (40.0, 55.0))
        self.assertEqual(memory.habit(30.02, 48.0, "2026-11-15", 30, reach=0), (None, 70.0), "one day is no habit")

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

    def sync(self, strong, rise=4.6, area=None):
        cfg = config()
        cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.0, 25.0, 57.6, 27.3], "enabled": True}]
        cfg["seawatch"].update(enabled=True, alerts=True)
        cfg["seawatch"]["early_alerts"]["enabled"] = True
        now = dt.datetime(2026, 11, 14, 15, 55, tzinfo=UTC)
        state = Mock()
        state.state = {"sea_candidates": [{"src": "mtg_spot", "t": "2026-11-14T15:40:00Z", "lat": 26.02,
                                           "lon": 55.90, "rise": rise, "strong": strong, "flame": True}]}
        state.incidents = []
        with patch.object(earlyalerts.preliminary, "sun_elevation", return_value=-30.0), \
                patch.object(earlyalerts.patterns, "platform_area", return_value=area):
            return earlyalerts.sync(cfg, state, now), state.state.get("early_night", {})

    def test_faint_first_picture_is_not_posted_alone(self):
        added, records = self.sync(strong=False)
        self.assertEqual((added, records), (0, {}))

    def test_unmistakable_first_picture_is(self):
        added, records = self.sync(strong=True)
        self.assertEqual(added, 1)
        self.assertEqual(len(records), 1)

    def test_among_known_platform_flares_one_picture_must_be_far_stronger(self):
        """Qatar's North Field, 24 Nov 2026: rise 21 then 50, NASA's flares 5 km away."""
        area = {"flare_km": 5.4, "spot_days": 8}
        self.assertEqual(self.sync(strong=True, rise=21.1, area=area)[0], 0, "waits for its second picture")
        added, records = self.sync(strong=True, rise=50.0, area=area)
        self.assertEqual(added, 1)
        record = next(iter(records.values()))
        text = earlyalerts._message(config(), record)
        self.assertIn("في منطقة منصات نفط وغاز: يرى سجل ناسا منذ 2024 شعلة معتادة على بعد 5 كم وحرارة في هذه النقطة في 8 أيام", text)
        for bad in ("هجوم", "استهداف", "ضربة"):
            self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
