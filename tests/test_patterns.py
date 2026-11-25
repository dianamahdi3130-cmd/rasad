"""Heat patterns (patterns.py): a label for heat at or near energy sites, in words that never name a
cause, shown in the app only. Pinned on the real events of 9-16 Nov 2026 (state branch, 16 Nov) and
on NOAA's real passes: the wide fire at Abqaiq of 26 Jul 2026 and a West Qurna night."""
import copy
import datetime as dt
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import h5py
import numpy as np

from rasad import archive, assess, brain, engine, noaafire, patterns
from rasad.brain import Memory, add_to_incident, alert_message, finalize, new_incident
from rasad.util import UTC, haversine_km, iso, load_json, save_json

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "patterns_week_20261116.json").read_text(encoding="utf-8"))
WEEK = {e["id"]: e for e in FIXTURE["events"]}
ABQAIQ = "20261115-0930_26.164_49.752"
JUDAH = "20261115-1740_25.609_48.874"
HARAD = "20261115-1020_24.221_49.329"
DUBAI = "20261111-0924_24.751_55.370"
SHEDGUM = "20261112-0440_25.638_49.392"
AHMADI = "20261115-0700_28.999_48.005"
UYUN = "20261110-0943_25.596_49.429"
BATEEN = "20261113-0150_22.431_53.979"
QURAYYAH = "20261110-1240_25.635_50.070"


def config(enabled=True):
    cfg = copy.deepcopy(engine.DEFAULTS)
    cfg["app_url"] = "https://app.example/"
    cfg["patterns"]["enabled"] = enabled
    return cfg


def week(iid, enabled=True, **extra):
    """A real event of the week as the check holds it: its passes rebuilt from its NASA readings."""
    inc = copy.deepcopy(WEEK[iid])
    inc.pop("_case", None)
    for o in sorted(inc["observations"], key=lambda o: o["t"]):
        patterns.note_pass(inc, o)
    inc.update(extra)
    return finalize(inc, config(enabled))


def pixel(t, lat, lon, frp=10.0, src="VIIRS_NOAA21_NRT", night=True, pa=None):
    d = {"src": src, "family": "viirs", "kind": "polar", "t": t, "lat": lat, "lon": lon, "frp": frp,
         "conf": "n", "night": night, "satellite": "N21", "instrument": "VIIRS"}
    if pa:
        d["pa"] = pa
    return d


def fire_from(rows, *geo, **fields):
    """An incident on land in Saudi Arabia made of NOAA rows and Meteosat pictures."""
    lat, lon = (rows[0]["lat"], rows[0]["lon"]) if rows else fields.pop("at", (26.1575, 49.7600))
    dets = sorted(list(rows) + [{"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": t, "lat": lat,
                                 "lon": lon, "frp": 30.0} for t in geo], key=lambda d: d["t"])
    inc = new_incident(dets[0], fields.pop("kind", "new"))
    for d in dets:
        add_to_incident(inc, d)
    inc.update(place="land", where="في السعودية", country="السعودية", regions=["gulf"], region_name="الخليج الفارسي",
               known_days=0, area_days=0, typical_frp=None, facility={"label": "معمل بقيق", "code": "oil", "km": 1.0})
    inc.update(fields)
    return finalize(inc, config())


class NoaaTag(unittest.TestCase):
    """NOAA marks places that burn all the time (a 2017 mask): 1 = oil and gas, 3 = solar panels."""

    def efire(self, tags):
        out = io.BytesIO()
        with h5py.File(out, "w") as f:
            f.create_dataset("y", data=np.zeros(1536, ">f4"))
            n = len(tags)
            for name, value, kind in (("FP_Latitude", 26.1667, "f4"), ("FP_Longitude", 49.7592, "f4"),
                                      ("FP_FireRadiativePower", 8.66, "f4"), ("FP_FireMask", 8, "i2"),
                                      ("FP_Day", 1, "i2"), ("FP_Line", 100, "i2"), ("FP_PixelArea", 0.554, "f4")):
                f.create_dataset(name, data=np.full(n, value, kind))
            if tags is not None:
                f.create_dataset("FP_PersistentAnomalyCategory", data=np.asarray(tags, "i2"))
        return out.getvalue()

    KEY = "VIIRS_EFIRE_VIIRSI_EDR/2026/11/15/EFIRE-VIIRSI_v1r3_n21_s202611150853098_e202611150854343_c202611150917000.nc"

    def test_the_tag_comes_with_the_pixel(self):
        rows = noaafire.rows_from(self.efire([1, 0, 3]), self.KEY, "n21", (40, 20, 60, 32))
        self.assertEqual([r.get("pa") for r in rows], [1, None, 3])

    def test_a_file_without_the_tag_reads_as_before(self):
        with h5py.File(io.BytesIO(self.efire([1])), "r"):
            pass
        out = io.BytesIO()
        with h5py.File(io.BytesIO(self.efire([1])), "r") as src, h5py.File(out, "w") as f:
            for name in src:
                if name != "FP_PersistentAnomalyCategory":
                    f.create_dataset(name, data=src[name][:])
        rows = noaafire.rows_from(out.getvalue(), self.KEY, "n21", (40, 20, 60, 32))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("pa", rows[0])

    def test_the_real_abqaiq_north_pixels_carry_the_oil_and_gas_tag(self):
        tags = [o.get("pa") for o in WEEK[ABQAIQ]["observations"] if o.get("pa")]
        self.assertEqual(set(tags), {1})
        self.assertGreaterEqual(len(tags), 7)


class PassFootprint(unittest.TestCase):
    def test_the_wide_fire_at_abqaiq_of_26_july(self):
        inc = {}
        for row in FIXTURE["abqaiq_20260726"]:
            patterns.note_pass(inc, row)
        foot = patterns.footprint(inc)
        self.assertEqual(foot["n"], 27)
        self.assertAlmostEqual(foot["km"], 3.0, delta=0.15)
        self.assertAlmostEqual(foot["mw"], 146, delta=1.0, msg="distinct strengths: NOAA repeats a 750 m value")
        self.assertEqual(inc["passes"][0]["pa"], [1])

    def test_a_repeated_strength_counts_once(self):
        inc = {}
        for k in range(4):
            patterns.note_pass(inc, pixel("2026-11-15T22:59:00Z", 26.16 + k * 0.004, 49.75, 9.23))
        self.assertEqual((inc["passes"][0]["n"], sum(inc["passes"][0]["mw"])), (4, 9.23))

    def test_one_satellite_within_ten_minutes_is_one_pass(self):
        inc = {}
        patterns.note_pass(inc, pixel("2026-11-15T22:58:00Z", 26.16, 49.75))
        patterns.note_pass(inc, pixel("2026-11-15T22:59:00Z", 26.17, 49.75))
        patterns.note_pass(inc, pixel("2026-11-15T22:59:00Z", 26.17, 49.76, src="VIIRS_NOAA20_NRT"))
        patterns.note_pass(inc, pixel("2026-11-16T10:13:00Z", 26.17, 49.76))
        self.assertEqual([(p["src"][6:12], p["n"]) for p in inc["passes"]],
                         [("NOAA21", 2), ("NOAA20", 1), ("NOAA21", 1)])

    def test_the_widest_pass_is_kept_and_old_positions_dropped(self):
        inc = {}
        for k in range(10):
            patterns.note_pass(inc, pixel("2026-11-01T22:00:00Z", 26.16 + k * 0.003, 49.75, 5.0 + k))
        for day in range(2, 20):
            patterns.note_pass(inc, pixel(f"2026-11-{day:02d}T22:00:00Z", 26.16, 49.75))
        self.assertEqual(len(inc["passes"]), patterns.MAX_PASSES)
        widest = max(inc["passes"], key=lambda p: p["n"])
        self.assertEqual((widest["t"], widest["n"]), ("2026-11-01T22:00:00Z", 10))
        self.assertNotIn("pts", widest, "a pass from long ago keeps its summary, not its positions")
        self.assertGreater(widest["km"], 2.5)

    def test_two_records_of_one_fire_keep_the_larger_half_of_a_pass(self):
        a, b = {}, {}
        for k in range(3):
            patterns.note_pass(a, pixel("2026-11-15T22:59:00Z", 26.16 + k * 0.004, 49.75, 3.0 + k))
        for k in range(6):
            patterns.note_pass(b, pixel("2026-11-15T22:59:00Z", 26.16 + k * 0.004, 49.75, 3.0 + k))
        patterns.note_pass(b, pixel("2026-11-16T10:13:00Z", 26.16, 49.75, src="VIIRS_NOAA20_NRT"))
        patterns.merge_passes(a, b)
        self.assertEqual([p["n"] for p in a["passes"]], [6, 1])


class UsualFootprint(unittest.TestCase):
    """A big field shows many pixels every night; only a pass far wider than the place's own habit
    is 'wide'."""

    def nights(self, rows, days, start="2026-11-01"):
        state = {}
        first = dt.date.fromisoformat(start)
        for k in range(days):
            day = (first + dt.timedelta(days=k)).isoformat()
            patterns.record_footprints(state, [dict(r, t=day + r["t"][10:]) for r in rows], day)
        return state

    def test_nothing_is_called_wide_while_the_habit_is_still_learnt(self):
        state = self.nights(FIXTURE["west_qurna_20261115"], 3)
        self.assertIsNone(patterns.usual_pixels(state, 31.0, 47.35, "2026-11-04"))

    def test_west_qurna_every_night_is_its_habit_not_a_wide_fire(self):
        # NOAA-21 over West Qurna, 15 Nov 22:58 UTC: 18 pixels over 31 km. An event there gathers the
        # pixels within about 6 km: 9 of them over 6.6 km, 67 MW, as wide as a fire by number alone
        rows = FIXTURE["west_qurna_20261115"]
        centre = max(rows, key=lambda r: sum(1 for q in rows if haversine_km(r["lat"], r["lon"], q["lat"], q["lon"]) <= 6))
        near = [q for q in rows if haversine_km(centre["lat"], centre["lon"], q["lat"], q["lon"]) <= 6]
        self.assertEqual(len(near), 9)
        state = self.nights(rows, 12)        # ...but every night is like that
        usual = patterns.usual_pixels(state, centre["lat"], centre["lon"], "2026-11-13")
        self.assertEqual(usual, 9.0)
        geo = ["2026-11-15T23:10:00Z", "2026-11-15T23:20:00Z"]
        fields = dict(kind="unusual", known_days=20, typical_frp=8.0,
                      facility={"label": "حقل غرب القرنة", "code": "oil", "km": 1.0}, country="العراق")
        self.assertNotEqual(fire_from(near, *geo, usual_px=usual, **fields)["pattern"]["key"], "wide")
        self.assertEqual(fire_from(near, *geo, usual_px=2.0, **fields)["pattern"]["key"], "wide",
                         "the same pass at a place that usually shows two pixels")

    def test_abqaiq_26_july_against_a_quiet_habit_is_wide(self):
        rows = FIXTURE["abqaiq_20260726"]
        inc = fire_from(rows, "2026-07-26T23:10:00Z", "2026-07-26T23:20:00Z", usual_px=2.0, known_days=1)
        self.assertEqual(inc["pattern"]["key"], "wide")
        line = inc["evidence"][-1]
        self.assertEqual(line["s"], "·")
        self.assertIn("27 نقطة ساخنة منفصلة في مرور واحد لقمر ناسا", line["text"])
        self.assertIn(patterns.NO_CAUSE, line["text"])

    def test_a_wide_fire_stays_wide_when_its_evidence_is_trimmed(self):
        # a fire that burns for days: passes beyond twelve and pictures beyond sixty are dropped
        inc = fire_from(FIXTURE["abqaiq_20260726"], "2026-07-26T23:10:00Z", "2026-07-26T23:20:00Z",
                        usual_px=2.0, known_days=1)
        self.assertEqual(inc["pattern"]["key"], "wide")
        start = dt.datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
        for k in range(16):                                 # a pass every 2.4 hours
            add_to_incident(inc, pixel(iso(start + dt.timedelta(hours=2.4 * k)), 25.93, 49.69, 20.0))
        for k in range(80):                                 # Meteosat every 10 minutes
            add_to_incident(inc, {"src": "mtg_frp", "family": "mtg", "kind": "geo", "lat": 25.93, "lon": 49.69,
                                  "frp": 30.0, "t": iso(start + dt.timedelta(hours=30, minutes=10 * k))})
        finalize(inc, config())
        self.assertFalse(patterns.second_look(inc, "2026-07-26T22:59:00Z", 3), "the evidence is gone...")
        self.assertEqual(inc["pattern"]["key"], "wide", "...the label is not")

    def test_without_a_second_look_one_wide_pass_is_not_enough(self):
        inc = fire_from(FIXTURE["abqaiq_20260726"], usual_px=2.0, known_days=1)
        self.assertNotEqual(inc["pattern"]["key"], "wide")

    def test_two_checks_of_one_pass_add_up(self):
        state = {}
        a = pixel("2026-11-15T22:59:00Z", 26.161, 49.751)
        b = pixel("2026-11-15T22:59:00Z", 26.162, 49.752)
        patterns.record_footprints(state, [a], "2026-11-15")
        self.assertEqual(list(state["footprint_days"].values()), [{"2026-11-15": 1}])
        patterns.record_footprints(state, [b], "2026-11-15")      # the pass's second file, next check
        self.assertEqual(list(state["footprint_days"].values()), [{"2026-11-15": 2}])
        patterns.record_footprints(state, [], "2026-11-17")
        self.assertEqual(state["footprint_passes"], {}, "a pass is added up for a day, then only its cell's number")


class WeekLabels(unittest.TestCase):
    """The real events of 9-16 Nov 2026."""

    def test_abqaiq_north_is_new_sustained_heat(self):
        inc = week(ABQAIQ)
        self.assertEqual(inc["pattern"]["key"], "new_sustained")
        text = inc["evidence"][-1]["text"]
        self.assertTrue(text.startswith("النمط: حرارة جديدة مستمرة"), text)
        self.assertIn("آخر حرارة هنا قبل 14 يوماً", text)
        self.assertIn("NOAA تصنّف هذه النقطة موقع نفط وغاز", text)
        self.assertIn(patterns.NO_CAUSE, text)

    def test_abqaiq_north_without_its_quiet_fortnight_is_not(self):
        # made before the patterns and never measured: heat on one earlier day, so not called quiet
        inc = copy.deepcopy(WEEK[ABQAIQ])
        inc.pop("quiet_days")
        inc.pop("_case")
        self.assertEqual(finalize(inc, config())["pattern"]["key"], "short")
        self.assertEqual(week(ABQAIQ, quiet_days=3)["pattern"]["key"], "short", "burned again within the week")

    def test_the_pumping_station_and_qurayyah_are_new_sustained_heat(self):
        self.assertEqual(week(JUDAH)["pattern"]["key"], "new_sustained")
        self.assertEqual(week(QURAYYAH)["pattern"]["key"], "new_sustained")

    def test_harad_one_picture_is_short(self):
        self.assertEqual(week(HARAD)["pattern"]["key"], "short")

    def test_the_dubai_post_of_11_november_was_sun_on_panels(self):
        # 15 NASA day pixels 1.4 km from the mapped solar park (Phase 3 - Field A); Meteosat was read at
        # 09:20 and 09:30 UTC and showed nothing
        inc = week(DUBAI, solar_km=1.4, geo_read_at_pass=True)
        self.assertEqual(inc["pattern"]["key"], "solar")
        self.assertIn("على بعد 1.4 كم من محطة طاقة شمسية مرسومة", inc["evidence"][-1]["text"])
        self.assertNotEqual(week(DUBAI, solar_km=1.4)["pattern"]["key"], "solar",
                            "without a Meteosat reading at the time, nothing says the ground was cold")
        self.assertNotEqual(week(DUBAI, solar_km=5.0, geo_read_at_pass=True)["pattern"]["key"], "solar")

    def test_plant_surges(self):
        for iid in (SHEDGUM, AHMADI):
            inc = week(iid)
            self.assertEqual(inc["pattern"]["key"], "surge", iid)
            self.assertIn("قد يكون حرق غاز تشغيلياً أو حريقاً", inc["evidence"][-1]["text"])

    def test_busy_areas(self):
        self.assertEqual(week(UYUN)["pattern"]["key"], "short", "one NASA pixel")
        bateen = week(BATEEN)
        self.assertEqual(bateen["pattern"]["key"], "busy", "five pictures in an area with heat on 21 days")
        self.assertIn("المنطقة حولها نشطة (حرارة في 21 يوماً خلال الشهر)", bateen["evidence"][-1]["text"])

    def test_a_long_event_is_never_called_short(self):
        inc = week(QURAYYAH, country="العراق",           # outside the dry Gulf countries, near a facility
                   facility={"label": "محطة كهرباء", "code": "power", "km": 5.0})
        self.assertEqual(inc["pattern"]["key"], "unclear")
        self.assertIn("استمرت نحو", inc["evidence"][-1]["text"])
        self.assertIn("خارج دول الخليج الجافة", inc["evidence"][-1]["text"])

    def test_no_more_days_are_claimed_than_the_memory_holds(self):
        self.assertIn("لم تُرصد حرارة هنا خلال 12 يوماً", week(JUDAH, memory_days=12)["evidence"][-1]["text"])
        self.assertIn("لم تُرصد حرارة هنا خلال 30 يوماً", week(JUDAH)["evidence"][-1]["text"])

    def test_less_than_an_hour_is_said_as_such(self):
        self.assertEqual(patterns._span(0.4), "استمرت أقل من ساعة")
        self.assertEqual(patterns._span(2), "استمرت نحو ساعتين")
        self.assertEqual(patterns._span(22), "استمرت نحو 22 ساعة")

    def test_nothing_this_week_was_wide_and_no_label_names_a_cause(self):
        for iid in WEEK:
            inc = week(iid, usual_px=0.0)
            self.assertNotEqual(inc["pattern"]["key"], "wide", iid)
            words = json.dumps(inc["pattern"], ensure_ascii=False)
            for cause in ("هجوم", "استهداف", "ضربة", "قصف", "مسيّرة"):
                self.assertNotIn(cause, words)

    def test_sea_and_farm_countries_have_no_label(self):
        sea = week(JUDAH, place="sea")
        self.assertNotIn("pattern", sea)
        self.assertFalse(any(e["text"].startswith(patterns.PREFIX) for e in sea["evidence"]))
        farm = week(QURAYYAH, country="العراق", facility=None)
        self.assertNotIn("pattern", farm)


class Silent(unittest.TestCase):
    """Nothing a post says, and nothing that decides a post, changes with the patterns on."""

    def test_posts_verdicts_and_numbers_are_the_same_with_the_patterns_off(self):
        for iid in WEEK:
            for extra in ({}, {"solar_km": 1.4, "geo_read_at_pass": True}):
                on, off = week(iid, True, **extra), week(iid, False, **extra)
                with self.subTest(iid=iid, extra=extra):
                    for key in ("verdict", "reason", "priority", "headline", "confidence", "confidence_level",
                                "severity", "category"):
                        self.assertEqual(on.get(key), off.get(key), key)
                    self.assertEqual(on.get("new_heat"), off.get("new_heat"))
                    self.assertEqual(brain.alert_worthy(on, config()), brain.alert_worthy(off, config(False)))
                    self.assertEqual(brain.new_heat(on, config()), brain.new_heat(off, config(False)))
                    self.assertEqual(assess.main_reason(on), assess.main_reason(off))
                    plain = lambda m: "\n".join(x for x in m.split("\n") if not x.startswith(("📈", "⚠️")))
                    self.assertEqual(plain(alert_message(on, config())), plain(alert_message(off, config(False))))
                    self.assertNotIn("pattern", off)
                    self.assertEqual(on["evidence"][:-1], off["evidence"][:len(on["evidence"]) - 1])

    def test_the_post_says_the_pattern_and_the_first_sign_but_never_a_cause(self):
        inc = {"headline": "حريق قرب منشأة نفطية", "pattern": {"key": "surge", "label": "اشتداد في موقع حرارة معتاد", "also": []},
               "signals": {"warn": [{"key": "sudden", "text": "بدأت فجأة وبقوة"}, {"key": "several", "text": "عدة مواقع"}]}}
        lines = brain.pattern_lines(inc)
        self.assertEqual(lines[0], "📈 <b>النمط:</b> اشتداد في موقع حرارة معتاد")
        self.assertIn("بدأت فجأة وبقوة", lines[1]); self.assertIn("و1 أخرى في التطبيق", lines[1])
        self.assertEqual(brain.pattern_lines({"pattern": {"key": "short", "label": "حرارة قصيرة أو متقطعة"}}), [],
                         "the everyday labels stay in the app")
        same = {"headline": "حرارة جديدة مستمرة في السعودية", "pattern": {"key": "new_sustained", "label": "حرارة جديدة مستمرة"}}
        self.assertEqual(brain.pattern_lines(same), [], "not said twice")
        for text in lines:
            for bad in ("هجوم", "استهداف", "ضربة"):
                self.assertNotIn(bad, text)

    def test_the_line_is_neutral_and_in_the_last_of_eight_places(self):
        inc = week(ABQAIQ)
        self.assertLessEqual(len(inc["evidence"]), 8)
        self.assertEqual([e["s"] for e in inc["evidence"]][-1], "·")
        self.assertEqual(sum(1 for e in inc["evidence"] if e["text"].startswith(patterns.PREFIX)), 1)
        again = finalize(inc, config())          # finalized again: still one line, not two
        self.assertEqual(sum(1 for e in again["evidence"] if e["text"].startswith(patterns.PREFIX)), 1)

    def test_the_archive_keeps_the_key(self):
        self.assertEqual(archive.slim(week(ABQAIQ))["pattern"], "new_sustained")
        self.assertNotIn("pattern", archive.slim(week(JUDAH, place="sea")))


class Posting(unittest.TestCase):
    """What the channel gets since 16 Nov 2026: heat that comes back to a quiet place and burns on
    (Abqaiq north), never sun glint on solar panels; strong single bursts and Judah's road unchanged."""
    CAUSES = ("هجوم", "استهداف", "ضربة", "قصف", "مسيّرة", "تخريب")

    def test_abqaiq_north_is_posted_as_new_sustained_heat(self):
        inc = week(ABQAIQ)
        self.assertEqual((inc["priority"], inc["new_heat"], inc["new_heat_path"]), ("high", True, "b"))
        self.assertEqual(inc["headline"], "حرارة جديدة مستمرة في موقع نفط وغاز في السعودية")
        message = alert_message(inc, config())
        self.assertIn("🗺 لا منشأة مسجّلة في الخريطة هنا. لم تُرصد حرارة في هذا المكان منذ 14 يوماً، ورُصدت فيه على "
                      "مدى نحو 22 ساعة. تصنّف NOAA هذه النقطة موقع نفط وغاز. سجل ناسا منذ آذار 2024: لا حرارة هنا ولا "
                      "حولها. صور الأقمار ترى الحرارة ولا ترى سببها.", message)
        self.assertNotIn("خلال شهر", message, "there was heat here two weeks before: never say a month")
        for cause in self.CAUSES:
            self.assertNotIn(cause, message)
        self.assertEqual(inc["evidence"][1]["text"], "لم تُرصد حرارة في هذا المكان منذ 14 يوماً، ورُصدت فيه على مدى "
                                                     "نحو 22 ساعة، ولا منشأة مسجّلة في الخريطة هنا")

    def test_without_noaas_tag_the_headline_says_heat_only(self):
        inc = copy.deepcopy(WEEK[ABQAIQ])
        inc.pop("_case")
        for o in inc["observations"]:
            o.pop("pa", None)
        for o in sorted(inc["observations"], key=lambda o: o["t"]):
            patterns.note_pass(inc, o)
        inc = finalize(inc, config())
        self.assertEqual(inc["headline"], "حرارة جديدة مستمرة في السعودية")
        self.assertNotIn("NOAA", alert_message(inc, config()))

    def test_the_road_needs_a_quiet_week_a_quiet_area_and_heat_that_burns_on(self):
        self.assertNotIn("new_heat", week(ABQAIQ, quiet_days=3), "burned here three days before")
        self.assertNotIn("new_heat", week(ABQAIQ, area_days=2), "the area around burns often")
        self.assertNotIn("new_heat", week(ABQAIQ, known_days=3), "a place that burns now and then")
        short = copy.deepcopy(WEEK[ABQAIQ])
        short.pop("_case")
        short["observations"] = [o for o in short["observations"] if o["t"] < "2026-11-15T10:30:00Z"]
        short["geo_slots"] = [t for t in short["geo_slots"] if t < "2026-11-15T10:30:00Z"]
        short["last"] = max(o["t"] for o in short["observations"])
        for o in sorted(short["observations"], key=lambda o: o["t"]):
            patterns.note_pass(short, o)
        self.assertNotIn("new_heat", finalize(short, config()), "an hour and a half is not 'burning on' yet")
        off = config()
        off["alerts"]["new_heat_sustained"] = False
        inc = copy.deepcopy(WEEK[ABQAIQ])
        inc.pop("_case")
        self.assertNotIn("new_heat", finalize(inc, off))

    def test_where_noaa_tags_oil_and_gas_three_hours_are_enough(self):
        rows = [pixel("2026-11-15T08:53:00Z", 26.1667, 49.7592, 8.66, pa=1),
                pixel("2026-11-15T09:50:00Z", 26.1642, 49.7615, 32.9, src="VIIRS_NOAA20_NRT", pa=1)]
        geo = ["2026-11-15T09:30:00Z", "2026-11-15T10:00:00Z", "2026-11-15T11:00:00Z", "2026-11-15T12:10:00Z"]
        inc = fire_from(rows, *geo, known_days=1, quiet_days=14, area_days=1, facility=None)
        self.assertEqual((inc["verdict"], inc["new_heat_path"], inc["priority"]), ("مؤكد", "b", "high"))
        untagged = fire_from([dict(r, pa=None) for r in rows], *geo, known_days=1, quiet_days=14, area_days=1,
                             facility=None)
        self.assertNotIn("new_heat", untagged, "elsewhere six hours and twelve pictures")

    def test_two_snapshots_hours_apart_do_not_burn_on(self):  # no pictures between them
        # a MODIS pixel at 07:30 and a tagged VIIRS pixel at 10:35, nothing between: confirmed by two
        # families, three hours apart, but not heat that burned on
        rows = [dict(pixel("2026-11-15T07:30:00Z", 26.1667, 49.7592, 12.0, src="MODIS_NRT"), family="modis"),
                pixel("2026-11-15T10:35:00Z", 26.1642, 49.7615, 14.0, pa=1)]
        inc = fire_from(rows, known_days=1, quiet_days=12, area_days=1, facility=None)
        self.assertEqual(inc["verdict"], "مؤكد")
        self.assertNotIn("new_heat", inc)
        self.assertEqual(inc["priority"], "low")

    def test_near_a_mapped_facility_the_facility_rule_decides(self):
        # the same heat 5 km from a mapped plant: under 50 MW it is not news, as before 16 Nov
        near = week(ABQAIQ, facility={"label": "معمل غاز", "code": "gas", "km": 5.0})
        self.assertEqual((near["priority"], near.get("new_heat")), ("low", None))
        self.assertEqual(near["headline"], "حريق قرب معمل غاز")
        self.assertEqual(near["pattern"]["key"], "new_sustained", "the app still says what it looks like")

    def test_where_no_earlier_day_had_heat_the_words_say_so(self):
        inc = week(ABQAIQ, known_days=0, quiet_days=None, memory_days=23)
        self.assertEqual(inc["new_heat_path"], "b")
        self.assertIn("لم تُرصد حرارة في هذا المكان خلال 23 يوماً، ورُصدت فيه على مدى نحو 22 ساعة",
                      alert_message(inc, config()))

    def test_heat_earlier_the_same_day_is_not_a_quiet_place(self):
        earlier = {"id": "x", "lat": 26.165, "lon": 49.753, "first": "2026-11-15T00:10:00Z", "last": "2026-11-15T01:00:00Z"}
        now_inc = {"id": "y"}
        self.assertTrue(patterns.near_earlier_heat([earlier], now_inc, 26.164, 49.752, "2026-11-15T13:30:00Z", 1))
        self.assertFalse(patterns.near_earlier_heat([earlier], now_inc, 26.164, 49.752, "2026-11-16T13:30:00Z", 1))
        self.assertFalse(patterns.near_earlier_heat([earlier], now_inc, 26.4, 49.752, "2026-11-15T13:30:00Z", 1))
        # one NASA pass, two groups 3 km apart made into two events: the same moment, not earlier heat
        sibling = {"id": "z", "lat": 26.194, "lon": 49.752, "first": "2026-11-15T10:35:00Z", "last": "2026-11-15T10:35:00Z"}
        self.assertFalse(patterns.near_earlier_heat([sibling], now_inc, 26.164, 49.752, "2026-11-15T10:35:00Z", 1))
        still = dict(earlier, last="2026-11-15T14:00:00Z")           # began earlier and still burning
        self.assertTrue(patterns.near_earlier_heat([still], now_inc, 26.164, 49.752, "2026-11-15T13:30:00Z", 1))

    def test_heat_the_day_before_is_said_without_a_zero(self):
        inc = week(HARAD, quiet_days=0)
        self.assertIn("رُصدت حرارة قربه خلال اليوم السابق", inc["evidence"][-1]["text"])
        self.assertNotIn("0 يوم", inc["evidence"][-1]["text"])

    def stretch_fire(self, pictures, pixels, **fields):
        geo = [iso(dt.datetime(2026, 11, 15, 0, 0, tzinfo=UTC) + dt.timedelta(minutes=m)) for m in pictures]
        rows = [pixel(iso(dt.datetime(2026, 11, 15, 0, 0, tzinfo=UTC) + dt.timedelta(minutes=m)), 26.1642, 49.7615,
                      14.0, pa=pa) for m, pa in pixels]
        return fire_from(rows, *geo, known_days=1, quiet_days=12, area_days=1, facility=None, **fields)

    def test_two_bursts_hours_apart_are_two_stretches(self):
        # Meteosat 08:00-08:50 and 15:00-15:50 (12 pictures), one NASA pixel: 8 hours first to last,
        # but two bursts of 50 minutes with six hours of nothing between
        inc = self.stretch_fire([480 + 10 * k for k in range(6)] + [900 + 10 * k for k in range(6)], [(500, None)])
        self.assertEqual(inc["verdict"], "مؤكد")
        self.assertNotIn("new_heat", inc)

    def test_a_long_gap_splits_even_where_noaa_tags_the_place(self):
        # tagged pixels at 07:30 and 12:00, four pictures 07:40-08:10: nothing for 3 hours 50 minutes
        inc = self.stretch_fire([460, 470, 480, 490], [(450, 1), (720, 1)])
        self.assertNotIn("new_heat", inc)
        joined = self.stretch_fire([460, 470, 480, 490, 600, 640], [(450, 1), (720, 1)])   # seen in between
        self.assertEqual(joined.get("new_heat_path"), "b")

    def test_each_stretch_is_judged_on_its_own(self):
        # a sparse flicker 00:00-07:00 (a picture every 80 minutes), four hours of nothing, then a
        # steady fire 11:00-17:10 with 38 pictures: the second stretch alone is heat that burns on
        sparse = [80 * k for k in range(6)] + [420]
        steady = [660 + 10 * k for k in range(38)]
        inc = self.stretch_fire(sparse + steady, [(30, None), (700, None)])
        self.assertEqual((inc.get("new_heat_path"), inc["priority"]), ("b", "high"))
        self.assertGreater(len(patterns.stretches(inc)), 1)

    def test_a_stretch_does_not_borrow_another_stretchs_pictures(self):
        # seven sparse hours (5 pictures and NASA passes between them), four hours of nothing, then a
        # one-hour burst of 10 pictures: neither stretch is heat that burns on, though together
        # they hold 15 pictures over seven hours
        sparse = [0, 100, 200, 300, 420]
        burst = [660 + 6 * k for k in range(10)]
        inc = self.stretch_fire(sparse + burst, [(50, None), (150, None), (250, None), (360, None)])
        self.assertEqual(inc["verdict"], "مؤكد")
        self.assertNotIn("new_heat", inc)

    def test_rasads_own_reading_gaps_do_not_break_a_fire(self):
        # Judah-like: pictures 17:40-18:40, no reading 18:45-20:50 (the check's own outage), pictures
        # again 21:40-23:40, NASA at 22:16 and 23:01: one stretch of six hours
        pictures = [1060 + 10 * k for k in range(7)] + [1300 + 10 * k for k in range(15)]
        inc = self.stretch_fire(pictures, [(1336, None), (1381, None)])
        self.assertEqual(len(patterns.stretches(inc)), 1)
        self.assertEqual(inc.get("new_heat_path"), "b")

    def test_the_road_of_15_november_and_the_strong_burst_are_unchanged(self):
        judah = week(JUDAH, facility=None)
        self.assertEqual((judah["new_heat_path"], judah["headline"]), ("a", "حريق جديد في السعودية"))
        self.assertIn("ولم تُرصد حرارة في هذا المكان ولا حوله خلال شهر", alert_message(judah, config()))
        harad = week(HARAD, facility=None)
        self.assertEqual((harad["new_heat_path"], harad["priority"], harad["headline"]),
                         ("strong", "high", "حريق جديد في السعودية"))
        self.assertEqual(harad["pattern"]["key"], "short", "posted, but not called sustained")

    def test_sun_glint_on_solar_panels_is_not_posted(self):
        self.assertEqual(week(DUBAI)["priority"], "high", "as it went out on 11 Nov")
        glint = week(DUBAI, solar_km=1.4, geo_read_at_pass=True)
        self.assertEqual(glint["priority"], "low")
        self.assertEqual(glint["pattern"]["key"], "solar")
        off = config()
        off["alerts"]["solar_glint_stop"] = False
        inc = copy.deepcopy(WEEK[DUBAI])
        inc.pop("_case")
        inc.update(solar_km=1.4, geo_read_at_pass=True)
        self.assertEqual(finalize(inc, off)["priority"], "high")

    def test_sun_glint_never_silences_an_energy_facility_or_a_surge(self):
        # Bahrain's Awali field, Taweelah, Al Ain power station: panels within 3 km of real plants
        at_plant = week(DUBAI, solar_km=2.5, geo_read_at_pass=True,
                        facility={"label": "منشأة نفطية", "code": "oil", "km": 0.2})
        self.assertEqual(at_plant["priority"], "high")
        nearer = week(DUBAI, solar_km=2.5, geo_read_at_pass=True,
                      facility={"label": "محطة كهرباء", "code": "power", "km": 2.2})
        self.assertNotEqual(nearer["pattern"]["key"], "solar", "the plant is nearer than the panels")
        surge = week(DUBAI, solar_km=1.4, geo_read_at_pass=True, kind="unusual", typical_frp=5.0, known_days=20)
        self.assertEqual(surge["priority"], "high")
        weak = week(DUBAI, solar_km=1.4, geo_read_at_pass=True, max_frp=30.0)
        self.assertNotEqual(weak["pattern"]["key"], "solar", "Meteosat misses 30 MW by day: its silence says little")

    def test_noaas_panel_tag_alone_never_takes_a_post_away(self):
        inc = copy.deepcopy(WEEK[DUBAI])
        inc.pop("_case")
        for o in inc["observations"]:
            o["pa"] = patterns.PA_SOLAR
        for o in sorted(inc["observations"], key=lambda o: o["t"]):
            patterns.note_pass(inc, o)
        inc.update(geo_read_at_pass=True)
        inc = finalize(inc, config())
        self.assertEqual(inc["priority"], "high")
        self.assertEqual(inc["pattern"]["key"], "solar", "the app may still say what it looks like")

    def test_the_video_line_promises_only_what_can_be_kept(self):
        self.assertEqual(brain.video_note({"geo_slots": ["2026-11-15T17:40:00Z"]}),
                         "🎞 يُرسل الفيديو هنا إن ظهر الحدث بوضوح في الصور.")


class SeveralPlaces(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 15, 20, 0, tzinfo=UTC)

    def two(self, minutes=40, km_lon=1.2, onset=(True, True), key="new_sustained"):
        a = {"id": "a", "first": "2026-11-15T17:40:00Z", "lat": 25.6, "lon": 48.9, "onset_known": onset[0],
             "pattern": {"key": key}}
        t = dt.datetime(2026, 11, 15, 17, 40, tzinfo=UTC) + dt.timedelta(minutes=minutes)
        b = {"id": "b", "first": iso(t), "lat": 25.6, "lon": 48.9 + km_lon, "onset_known": onset[1],
             "pattern": {"key": key}}
        return a, b

    def test_two_new_heats_within_hours_are_tagged(self):
        a, b = self.two()
        changed = patterns.link_sites([a, b], config(), self.NOW)
        self.assertEqual({i["id"] for i in changed}, {"a", "b"})
        self.assertEqual((a["linked"]["id"], a["linked"]["min"], a["linked"]["km"]), ("b", 40, 120))
        self.assertEqual(patterns.link_sites([a, b], config(), self.NOW), [], "unchanged: nothing to write again")

    def test_after_a_gap_in_the_readings_starts_are_not_known(self):
        a, b = self.two(onset=(True, False))
        self.assertEqual(patterns.link_sites([a, b], config(), self.NOW), [])

    def test_surges_too_close_too_far_or_too_late_are_not_tagged(self):
        for a, b in (self.two(key="surge"), self.two(km_lon=0.02), self.two(km_lon=6.0), self.two(minutes=200)):
            self.assertEqual(patterns.link_sites([a, b], config(), self.NOW), [])

    def test_an_older_partner_keeps_its_tag_and_is_still_found(self):
        a, b = self.two(minutes=150)
        patterns.link_sites([a, b], config(), self.NOW)
        later = self.NOW + dt.timedelta(days=2, hours=1)      # a is older than two days, b is not
        self.assertEqual(patterns.link_sites([a, b], config(), later), [])
        self.assertEqual((a["linked"]["id"], b["linked"]["id"]), ("b", "a"))

    def test_the_tag_only_belongs_to_new_sustained_or_wide_heat(self):
        inc = week(SHEDGUM, linked={"id": "x", "km": 120, "min": 40})
        self.assertEqual(inc["pattern"]["also"], [])
        self.assertNotIn(patterns.LINKED, inc["evidence"][-1]["text"])

    def test_places_seen_in_the_same_picture(self):
        inc = week(JUDAH, linked={"id": "x", "km": 120, "min": 0})
        self.assertIn("على بعد 120 كم في الوقت نفسه", inc["evidence"][-1]["text"])

    def test_a_start_is_known_only_from_the_same_product(self):
        start = {"src": "mtg_frp", "kind": "geo", "t": "2026-11-15T21:40:00Z", "lat": 25.6, "lon": 48.9}
        other = {"iodc_fire|46.0,18.35,53.0,24.7|2026-11-15T21:15:00Z": 25.0,
                 "iodc_fire|46.0,24.7,53.0,31.05|2026-11-15T21:15:00Z": 25.0}
        inc = {}
        patterns.onset_facts(inc, start, other)
        self.assertIs(inc["onset_known"], False, "Meteosat-9 read; Meteosat-12 may have had a gap")
        patterns.onset_facts(inc, start, dict(other, **{"mtg_frp|48.0,25.0,50.0,27.0|2026-11-15T21:10:00Z": 9.0}))
        self.assertIs(inc["onset_known"], True)
        patterns.onset_facts(inc, start, {"mtg_frp|48.0,25.0,50.0,27.0|2026-11-15T21:35:00Z": 9.0})
        self.assertIs(inc["onset_known"], False, "read 5 minutes before: too close to say the start")

    def test_the_tag_is_said_in_the_reasons_line(self):
        inc = week(JUDAH, linked={"id": "x", "km": 120, "min": 40})
        text = inc["evidence"][-1]["text"]
        self.assertIn("حرارة جديدة مستمرة · عدة مواقع في وقت متقارب", text)
        self.assertIn("ظهرت حرارة جديدة في موقع آخر على بعد 120 كم خلال 40 دقيقة", text)


class Signals(unittest.TestCase):
    """«مؤشرات تستحق الانتباه»: facts that make heat at an energy site worth a closer look, and facts
    that make it look like the site's everyday heat. In the app only, never a cause."""

    @staticmethod
    def atlas(**cells):
        """A small long-term map: 200 sampled dates; a flare at 26.170,49.760 hot on 120 of them."""
        A = {"cell": 0.005, "dates": 200, "first": "2024-03-05", "cells": {}}
        def put(lat, lon, n):
            A["cells"][f"{math.floor(lat / 0.005)},{math.floor(lon / 0.005)}"] = n
        put(26.170, 49.760, 120)
        for (lat, lon), n in cells.items():
            put(lat, lon, n)
        return A

    def signals(self, rows, *geo, A=None, **fields):
        inc = fire_from(rows, *geo, **fields)
        return patterns.signals_of(inc, config(), self.atlas() if A is None else A), inc

    def test_heat_at_a_spot_of_the_plant_that_rarely_burns(self):
        # a tank farm 1.4 km from the plant's flare, which burns on 60% of the dates
        rows = [pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)]
        sig, inc = self.signals(rows)
        self.assertEqual(sig["n"], 1)
        self.assertEqual(sig["warn"][0]["key"], "odd_spot")
        self.assertIn("الحرارة في نقطة لم تظهر فيها حرارة إلا نادراً منذ آذار 2024", sig["warn"][0]["text"])
        self.assertIn("بينما أقرب مكان تظهر فيه حرارة عادةً على بعد 1.7 كم", sig["warn"][0]["text"])
        self.assertEqual(sig["note"], patterns.SIGNALS_NOTE)

    def test_a_faint_unconfirmed_speck_at_an_odd_spot_is_said_not_counted(self):
        # on the week of 9-16 Nov 2026, 31 of the 36 odd-spot events were specks of 1-6 MW seen once
        sig, inc = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 1.5)])
        self.assertNotEqual(inc["verdict"], "مؤكد")
        self.assertEqual(sig["n"], 0)
        self.assertEqual([w["key"] for w in sig["wait"]], ["odd_spot_weak"])
        self.assertTrue(sig["wait"][0]["text"].endswith("؛ لكنها ضعيفة أو غير مؤكدة"))

    def test_heat_at_the_plants_own_flare_is_said_to_be_its_habit(self):
        sig, _ = self.signals([pixel("2026-11-15T22:59:00Z", 26.1702, 49.7602, 30.0)])
        self.assertEqual((sig["n"], [c["key"] for c in sig["calm"]]), (0, ["usual_spot"]))
        self.assertEqual(sig["calm"][0]["text"], "الحرارة في مكان تظهر فيه حرارة عادةً (شعلة أو مصنع): في نحو 60% من "
                                                 "الأيام منذ آذار 2024")

    def test_only_meteosat_saw_it_so_the_spot_is_not_known_yet(self):
        sig, _ = self.signals([], "2026-11-15T22:50:00Z", "2026-11-15T23:00:00Z")
        self.assertEqual([w["key"] for w in sig["wait"]], ["spot_unknown"])
        self.assertEqual(sig["n"], 0)

    def test_an_atlas_too_short_or_missing_says_nothing_about_spots(self):
        short = dict(self.atlas(), dates=20)
        sig, _ = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], A=short)
        self.assertTrue(sig is None or not any(w["key"] == "odd_spot" for w in sig["warn"]))
        sig, _ = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], A={})
        self.assertTrue(sig is None or not sig["warn"])

    def test_a_sudden_strong_start_at_a_quiet_place(self):
        geo = ["2026-11-15T22:50:00Z", "2026-11-15T23:00:00Z", "2026-11-15T23:10:00Z"]
        sig, inc = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], *geo, onset_known=True,
                                quiet_days=14)
        self.assertNotIn("sudden", [w["key"] for w in sig["warn"]], "the first half hour is not over yet")
        self.assertNotIn("start_mw", inc)
        geo.append("2026-11-15T23:40:00Z")
        sig, inc = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], *geo, onset_known=True,
                                quiet_days=14)
        self.assertEqual(next(w["text"] for w in sig["warn"] if w["key"] == "sudden"),
                         "بدأت فجأة وبقوة: 30 MW خلال أول نصف ساعة من ظهورها، ولم تظهر في صورة Meteosat السابقة لها")
        self.assertEqual(inc["start_mw"], 30.0, "kept: a trimmed strength series cannot change it")
        sig, _ = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], *geo, onset_known=False)
        self.assertNotIn("sudden", [w["key"] for w in sig["warn"]], "after a gap in the readings the start is not known")
        sig, _ = self.signals([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], *geo, onset_known=True, quiet_days=0)
        self.assertNotIn("sudden", [w["key"] for w in sig["warn"]], "heat here the day before")

    def test_several_places_wide_heat_and_a_change_in_the_picture_after(self):
        rows = [pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)]
        inc = fire_from(rows, "2026-11-15T23:10:00Z", "2026-11-15T23:20:00Z", linked={"id": "x", "km": 120, "min": 40},
                        wide={"n": 27, "km": 3.0, "mw": 142.0, "t": "2026-11-15T22:59:00Z", "usual": 2.0},
                        satpic={"before": "2026-11-10", "after": "2026-11-17", "area_ha": 8.1, "quiet": False},
                        known_days=0, quiet_days=None)
        sig = patterns.signals_of(inc, config(), self.atlas())
        keys = [w["key"] for w in sig["warn"]]
        self.assertEqual(set(keys), {"odd_spot", "several", "wide"}, keys)
        self.assertIn("انتشرت على 27 نقطة ساخنة على امتداد 3.0 كم في مرور واحد لقمر ناسا", " ".join(w["text"] for w in sig["warn"]))
        # the before/after picture measures change over the whole frame, not at the heat: not a signal (yet)
        self.assertNotIn("change", keys)

    def test_a_plants_surge_to_a_level_it_reached_before(self):
        sig, _ = self.signals([], "2026-11-16T12:40:00Z", kind="unusual", known_days=24, typical_frp=19.6,
                              site_record=130.7, site_habit=95.9)
        self.assertEqual([c["text"] for c in sig["calm"]],
                         ["بلغت حرارة هذا المكان 131 MW في يوم سابق من الشهر، وأعلى ما بلغه هذا الحدث 30 MW"])
        # Ras Laffan, 16 Nov: 206 MW against a record of 131: the record is not called its habit
        rows = [pixel("2026-11-16T12:14:00Z", 26.1702, 49.7602, 206.0)]
        sig, _ = self.signals(rows, kind="unusual", known_days=24, typical_frp=19.6, site_record=130.7, site_habit=95.9)
        self.assertNotIn("record", [c["key"] for c in sig["calm"]])

    def test_sea_and_far_away_land_get_no_signals(self):
        rows = [pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)]
        self.assertIsNone(self.signals(rows, place="sea")[0])
        far, _ = self.signals([pixel("2026-11-15T22:59:00Z", 31.0, 46.0, 30.0)], facility=None, country="العراق")
        self.assertIsNone(far)

    def test_modis_is_too_coarse_to_say_which_spot_burns(self):
        rows = [dict(pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0, src="MODIS_NRT"), family="modis", scan=1.8)]
        sig, _ = self.signals(rows)
        self.assertEqual((sig["n"], [w["key"] for w in sig["wait"]]), (0, ["spot_unknown"]))
        self.assertIn("MODIS", sig["wait"][0]["text"])

    def test_a_late_pixel_of_an_earlier_day_is_not_a_new_day(self):
        state = {}
        patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, "2026-11-10")
        patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, "2026-11-11")
        patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, "2026-11-10")   # FIRMS late, an earlier day
        patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, "2026-11-11")
        self.assertEqual(list(state["spots"].values())[0][0], 2)

    def test_rasads_own_record_learns_a_new_flare(self):
        # a flare lit after the long-term map was built: Rasad sees it day after day
        state = {}
        for k in range(12):
            day = (dt.date(2026, 11, 1) + dt.timedelta(days=k)).isoformat()
            patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, day)
            patterns.record_spot(state, {"lat": 26.1576, "lon": 49.7601}, day)        # the same day twice: one day
        self.assertEqual(patterns.spot_days(state, 26.1575, 49.7600, "2026-11-13"), 12)
        self.assertEqual(patterns.spot_days(state, 26.1575, 49.7600, "2026-11-01"), 0, "first seen that day: not usual yet")
        self.assertEqual(patterns.spot_days(state, 26.20, 49.7600, "2026-11-13"), 0, "4.7 km away")
        rows = [dict(pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0), spot=12)]
        sig, _ = self.signals(rows)
        self.assertEqual(sig["n"], 0, "Rasad has seen this spot burn on 12 days")
        self.assertEqual(sig["calm"][0]["key"], "usual_spot")
        rows = [dict(pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0), spot=1)]
        self.assertEqual(self.signals(rows)[0]["n"], 1, "seen once before: still rare")

    def test_quiet_spots_are_forgotten_after_half_a_year(self):
        state = {}
        for k in range(5):     # a flare seen on five days in January, then quiet
            patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, (dt.date(2026, 1, 1) + dt.timedelta(days=k)).isoformat())
        patterns.record_spot(state, {"lat": 26.30, "lon": 49.7600}, "2026-10-01")     # a one-off spot in October
        patterns.record_spot(state, {"lat": 26.40, "lon": 49.7600}, "2026-10-01")
        patterns.record_spot(state, {"lat": 26.40, "lon": 49.7600}, "2026-10-02")
        for k in range(8):
            patterns.record_spot(state, {"lat": 30.0, "lon": 48.0}, (dt.date(2026, 11, 10) + dt.timedelta(days=k)).isoformat())
        self.assertEqual(len(state["spots"]), 1, "the January flare (half a year quiet) and October's one-offs are gone")

    def test_a_damaged_map_is_left_aside_never_stops_a_check(self):
        import gzip as gz
        with tempfile.TemporaryDirectory() as folder:
            for content in (b"\x1f\x8b\x08\x00broken", None):
                path = Path(folder) / "heat_atlas.json.gz"
                if content is None:
                    with gz.open(path, "wt") as f:
                        f.write('["not", "a", "map"]')
                else:
                    path.write_bytes(content)
                with patch.object(patterns, "ATLAS_PATH", path), patch.object(patterns, "_atlas", None):
                    self.assertEqual(patterns.atlas(), {})
                    inc = fire_from([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)])
                    self.assertEqual(inc["verdict"], "مرجّح")
        with patch.object(patterns, "signals_of", Mock(side_effect=KeyError("cells"))):
            inc = {"id": "x"}
            patterns.refresh_signals(inc, config())
            self.assertNotIn("signals", inc)

    def test_a_fire_burning_for_days_does_not_make_its_own_spot_usual(self):
        state = {}
        for k in range(12):        # the fire itself, every day from 1 Nov
            patterns.record_spot(state, {"lat": 26.1575, "lon": 49.7600}, (dt.date(2026, 11, 1) + dt.timedelta(days=k)).isoformat())
        self.assertEqual(patterns.spot_days(state, 26.1575, 49.7600, "2026-11-01"), 0, "counted from the event's first day")
        self.assertEqual(patterns.spot_days(state, 26.1575, 49.7600, "2026-11-12"), 11, "for a later event, its habit")

    def test_a_sudden_start_waits_for_its_first_strengths(self):
        geo = ["2026-11-15T22:50:00Z", "2026-11-15T23:00:00Z", "2026-11-15T23:10:00Z", "2026-11-15T23:40:00Z"]
        inc = fire_from([pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)], *geo, onset_known=True, quiet_days=14)
        inc.pop("start_mw", None)
        for o in inc["observations"]:
            if o["kind"] == "geo" and o["t"] < "2026-11-15T23:20:00Z":
                o["frp"] = None                   # the strengths of the first half hour are still being read
        self.assertIsNone(patterns.sudden_start(inc))
        self.assertNotIn("start_mw", inc, "not decided yet")
        for o in inc["observations"]:
            if o["kind"] == "geo":
                o["frp"] = 45.0
        self.assertEqual(patterns.sudden_start(inc), 45.0)

    def test_modis_with_meteosat_is_not_called_alone_among_all_satellites(self):
        rows = [dict(pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0, src="MODIS_NRT"), family="modis", scan=1.8)]
        sig, inc = self.signals(rows, "2026-11-15T22:50:00Z", "2026-11-15T23:00:00Z")
        self.assertEqual(sig["wait"][0]["text"], "من أجهزة ناسا رآها MODIS وحده (دقته نحو 1 كم)، فلا يُعرف من أيّ نقطة في "
                                                 "المكان تأتي الحرارة")

    def test_in_a_check_each_pixel_carries_its_spots_days_before_the_event(self):
        state = {"spots": {}}
        for k in range(12):        # a flare Rasad saw on 12 days before this event
            patterns.record_spot(state, {"lat": 26.1693, "lon": 49.7580}, (dt.date(2026, 11, 1) + dt.timedelta(days=k)).isoformat())
        rows = [pixel("2026-11-15T22:59:00Z", 26.1693, 49.758, 9.23), pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 9.0)]
        cfg = copy.deepcopy(engine.DEFAULTS)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
            cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [47, 22, 57, 30]}]
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["eumetview"]["fire_layers"] = []
            cfg["clip"]["enabled"] = False
            cfg["firms"]["map_key"] = "offline-test-key"
            cfg["noaa"]["enabled"] = True
            geo = Mock(version="offline-map")
            geo.place_kind.return_value = "land"
            geo.facility.return_value = {"label": "معمل بقيق", "code": "oil", "km": 1.0}
            geo.describe.return_value = ("في السعودية", "السعودية")
            geo.solar_km.return_value = None
            geo.facility_within.return_value = True
            now = dt.datetime(2026, 11, 15, 23, 30, tzinfo=UTC)
            save_json(root / "state" / "state.json", dict(state, schema=3, rules=engine.RULES, clip_style=engine.CLIP_STYLE,
                                                          sea_check=engine.SEA_CHECK, bootstrapped=iso(now), hello_sent=iso(now)))
            memory = Memory({})
            memory.record(30, 48, "2026-11-14", 10)
            save_json(root / "state" / "memory.json", memory.cells)
            forbidden = Mock(side_effect=AssertionError("Unexpected source"))
            with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(), WMS=Mock(),
                                Geo=Mock(return_value=geo), Availability=Mock(), firms_fetch=Mock(return_value=[]),
                                latest_time=forbidden, detect_geo_layer=forbidden, deliver=Mock(return_value=None),
                                tg_send=forbidden), \
                    patch.object(engine.noaafire, "collect", Mock(return_value=copy.deepcopy(rows))):
                S = engine.run_cycle(cfg, send=False)
            spots = sorted(o.get("spot") for i in S.incidents for o in i["observations"] if o["kind"] == "polar")
            self.assertEqual(spots, [0, 12], "the flare's 12 days; the new spot 0 (this event's own day does not count)")
            self.assertEqual(len(load_json(root / "state" / "state.json", {})["spots"]), 2)

    def test_no_signal_ever_names_a_cause_and_posts_do_not_change(self):
        rows = [pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, 30.0)]
        with patch.object(patterns, "_atlas", self.atlas()):
            inc = fire_from(rows, "2026-11-15T22:50:00Z", "2026-11-15T23:00:00Z", onset_known=True, quiet_days=14)
            self.assertGreaterEqual(inc["signals"]["n"], 1)
            text = json.dumps(inc["signals"], ensure_ascii=False)
            for cause in ("هجوم", "استهداف", "ضربة", "قصف", "مسيّرة", "تخريب"):
                self.assertNotIn(cause, text)
            message = alert_message(inc, config())
            # since 22 Nov 2026 the post carries the first sign worth attention, as one neutral line
            sign = [x for x in message.split("\n") if x.startswith("⚠️")]
            self.assertEqual(len(sign), 1)
            self.assertIn("علامة تستحق الانتباه", sign[0])
            for cause in ("هجوم", "استهداف", "ضربة", "قصف", "مسيّرة", "تخريب"):
                self.assertNotIn(cause, message)
            off = config()
            off["signals"]["enabled"] = False
            again = finalize(copy.deepcopy(inc), off)
            self.assertNotIn("signals", again)
            self.assertEqual((again["priority"], again["headline"], again["confidence"]),
                             (inc["priority"], inc["headline"], inc["confidence"]), "a sign never decides a post")
            self.assertEqual(alert_message(again, off), "\n".join(x for x in message.split("\n") if not x.startswith("⚠️")))


class SignPosts(unittest.TestCase):
    """A confirmed fire on land in the Gulf states carrying a sign worth attention is posted wherever
    it is (the owner's decision of 24 Nov 2026), with no floor of its own: a 7 MW fire like the one
    near a Jizan power plant is not left out for being small."""

    def fire(self, mw=7.0, pictures=2, **fields):
        # 1.4 km from a flare that burns on 60% of the dates, 6 km from the mapped facility, in an area
        # with heat on earlier days: none of the plain rules posts it
        dets = [pixel("2026-11-15T22:59:00Z", 26.1575, 49.7600, mw)]
        dets += [{"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": f"2026-11-15T23:{10 * k:02d}:00Z",
                  "lat": 26.1575, "lon": 49.7600, "frp": mw} for k in range(pictures)]
        inc = new_incident(dets[0], "new")
        for d in dets:
            add_to_incident(inc, d)
        inc.update(place="land", where="في السعودية", country="السعودية", regions=["gulf"],
                   region_name="الخليج الفارسي", known_days=0, area_days=3, typical_frp=None,
                   facility={"label": "معمل بقيق", "code": "oil", "km": 6.0})
        inc.update(fields)
        cfg = fields.pop("_cfg", None) or config()
        with patch.object(patterns, "_atlas", Signals.atlas()):
            return finalize(inc, cfg), cfg

    def test_a_confirmed_fire_at_a_spot_that_rarely_burns_is_posted_from_its_sign(self):
        inc, cfg = self.fire()
        self.assertEqual(inc["verdict"], "مؤكد")
        self.assertFalse(brain.alert_worthy(inc, cfg), "the plain rules leave it out (busy area, 6 km away)")
        self.assertIn("odd_spot", [w["key"] for w in inc["signals"]["warn"]])
        self.assertEqual(inc["priority"], "high")
        message = alert_message(inc, cfg)
        self.assertIn("⚠️ <b>علامة تستحق الانتباه:</b>", message)
        for cause in ("هجوم", "استهداف", "ضربة"):
            self.assertNotIn(cause, message)

    def test_not_without_the_confirmation_the_sign_the_gulf_or_the_switch(self):
        inc, _ = self.fire(pictures=0)
        self.assertNotEqual(inc["verdict"], "مؤكد")
        self.assertEqual(inc["priority"], "low", "one sighting waits")
        inc, _ = self.fire(mw=4.0)
        self.assertEqual(inc["priority"], "low", "under the sign's own 5 MW there is no sign")
        inc, _ = self.fire(country="العراق", where="في العراق")
        self.assertEqual(inc["priority"], "low", "farm countries stay out")
        cfg = config()
        cfg["alerts"]["sign_alerts"] = False
        inc, _ = self.fire(_cfg=cfg)
        self.assertEqual(inc["priority"], "low")

    def test_recovered_past_events_are_never_news(self):
        inc, _ = self.fire(recovered_historical=True)
        self.assertEqual(inc["priority"], "low")


class InACheck(unittest.TestCase):
    """A whole check keeps what the patterns need when it makes an incident, and the app gets the label."""
    NOW = dt.datetime(2026, 11, 15, 22, 50, tzinfo=UTC)

    def run_check(self, root, noaa_rows, slots=None, now=None, rules=None, facility=None):
        now = now or self.NOW
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [47, 22, 57, 30]}]
        cfg["seawatch"].update(enabled=False, look_back=False)
        cfg["eumetview"]["fire_layers"] = []
        cfg["clip"]["enabled"] = False
        cfg["firms"]["map_key"] = "offline-test-key"
        cfg["noaa"]["enabled"] = True
        session = Mock()
        session.get.side_effect = AssertionError("No network")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "land"
        geo.facility.return_value = facility or {"label": "معمل بقيق", "code": "oil", "km": 1.0}
        geo.describe.return_value = ("في السعودية", "السعودية")
        geo.solar_km.return_value = None
        state = {"schema": 3, "rules": rules or engine.RULES, "clip_style": engine.CLIP_STYLE, "sea_check": engine.SEA_CHECK,
                 "bootstrapped": iso(now), "hello_sent": iso(now), "slots": slots or {}}
        save_json(root / "state" / "state.json", state)
        memory = Memory({})
        memory.record(26.16, 49.75, "2026-11-01", 9.0)    # heat here on 1 Nov, a fortnight before
        memory.record(30, 48, "2026-11-14", 10)
        save_json(root / "state" / "memory.json", memory.cells)
        forbidden = Mock(side_effect=AssertionError("Unexpected source"))
        with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(return_value=session),
                            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
                            firms_fetch=Mock(return_value=[]), latest_time=forbidden, detect_geo_layer=forbidden,
                            deliver=Mock(return_value=None), tg_send=forbidden), \
                patch.object(engine.noaafire, "collect", Mock(return_value=copy.deepcopy(noaa_rows))):
            return engine.run_cycle(cfg, send=False)

    ROWS = [pixel("2026-11-15T22:59:00Z", 26.1693, 49.758, 9.23, pa=1),
            pixel("2026-11-15T22:59:00Z", 26.1686, 49.7611, 9.23, pa=1),
            pixel("2026-11-15T22:59:00Z", 26.1637, 49.7597, 3.43, pa=1)]

    def test_an_incident_keeps_its_facts_and_the_app_gets_its_label(self):
        slots = {"mtg_frp|49.0,25.0,51.0,27.0|2026-11-15T22:50:00Z": 9.0}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            S = self.run_check(root, self.ROWS, slots)
            self.assertEqual(len(S.incidents), 1)
            inc = S.incidents[0]
            self.assertEqual(inc["quiet_days"], 14)
            self.assertIsNone(inc["usual_px"], "the usual footprint is still being learnt")
            self.assertIsNone(inc["solar_km"])
            self.assertIs(inc["geo_read_at_pass"], True, "Meteosat was read here at 22:50")
            self.assertEqual(inc["passes"][0]["n"], 3)
            self.assertEqual(inc["pattern"]["key"], "short", "one pass: not sustained yet")
            state = load_json(root / "state" / "state.json", {})
            self.assertEqual(state["footprint_since"], "2026-11-15")
            self.assertEqual(sum(sum(d.values()) for d in state["footprint_days"].values()), 3)
            public = load_json(root / "site" / "data" / "incidents.json", [])
            items = public["items"]
            self.assertEqual(items[0]["pattern"]["label"], "حرارة قصيرة أو متقطعة")
            self.assertNotIn("passes", items[0], "the pass summaries stay in the memory")

    def test_events_made_before_the_patterns_are_labelled_once(self):
        stored = copy.deepcopy(WEEK[ABQAIQ])
        stored.pop("_case")
        stored.pop("quiet_days")
        finalize(stored, config(False))                     # as the check stored it before this version
        before = {k: stored.get(k) for k in ("verdict", "priority", "headline", "confidence", "status")}
        self.assertNotIn("pattern", stored)
        cfg = config()
        patterns.label_stored([stored], cfg)
        self.assertEqual(stored["pattern"]["key"], "short", "quiet days not measured yet: not called quiet")
        self.assertEqual(stored["passes"][0]["n"], 2)
        self.assertEqual({k: stored.get(k) for k in before}, before)
        patterns.label_stored([stored], cfg)
        self.assertEqual(sum(1 for e in stored["evidence"] if e["text"].startswith(patterns.PREFIX)), 1)

    def test_a_check_labels_the_stored_events_and_measures_their_quiet(self):
        stored = copy.deepcopy(WEEK[ABQAIQ])
        stored.pop("_case")
        stored.pop("quiet_days")
        finalize(stored, config(False))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / "state" / "incidents.json", [stored])
            S = self.run_check(root, [], now=dt.datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
            inc = next(i for i in S.incidents if i["id"] == ABQAIQ)
            self.assertEqual(inc["quiet_days"], 14)
            self.assertEqual(inc["pattern"]["key"], "new_sustained")
            self.assertEqual(load_json(root / "state" / "state.json", {})["patterns"], engine.PATTERNS)

    def test_the_new_rules_do_not_post_abqaiq_a_day_late(self):
        # 16 Nov 09:00 UTC, the first check under the new rules: Abqaiq north is news now, but it
        # started 24 hours before; it is marked late and shown in the app, never posted
        stored = copy.deepcopy(WEEK[ABQAIQ])
        stored.pop("_case")
        old = config()
        old["alerts"]["new_heat_sustained"] = False
        finalize(stored, old)
        self.assertEqual(stored["priority"], "low")
        stored["status"] = "new"
        far = {"label": "محطة كهرباء (AC Plant #2)", "code": "power", "km": 25.6}
        stored["_pos"] = [round(stored["lat"], 3), round(stored["lon"], 3), "offline-map"]
        stored["facility"] = far
        deliver = Mock(return_value=None)
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, "deliver", deliver):
            root = Path(folder)
            save_json(root / "state" / "incidents.json", [stored])
            S = self.run_check(root, [], now=dt.datetime(2026, 11, 16, 9, 0, tzinfo=UTC), rules=19, facility=far)
            inc = next(i for i in S.incidents if i["id"] == ABQAIQ)
            self.assertEqual((inc["priority"], inc.get("new_heat"), inc["status"]), ("high", True, "late"))
            deliver.assert_not_called()

    def test_a_failing_fact_never_stops_the_check(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine.patterns, "usual_pixels", Mock(side_effect=RuntimeError("broken"))):
            S = self.run_check(Path(folder), self.ROWS)
            self.assertEqual(len(S.incidents), 1)


if __name__ == "__main__":
    unittest.main()
