"""Heat patterns (patterns.py): a label for heat at or near energy sites, in words that never name a
cause, shown in the app only. Pinned on the real events of 9-16 Nov 2026 (state branch, 16 Nov) and
on NOAA's real passes: the wide fire at Abqaiq of 26 Jul 2026 and a West Qurna night."""
import copy
import datetime as dt
import io
import json
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
    dets = sorted(list(rows) + [{"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": t, "lat": rows[0]["lat"],
                                 "lon": rows[0]["lon"], "frp": 30.0} for t in geo], key=lambda d: d["t"])
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

    def test_busy_areas_are_short(self):
        for iid in (UYUN, BATEEN):
            self.assertEqual(week(iid)["pattern"]["key"], "short", iid)

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
                    self.assertEqual(alert_message(on, config()), alert_message(off, config(False)))
                    self.assertNotIn("pattern", off)
                    self.assertEqual(on["evidence"][:-1], off["evidence"][:len(on["evidence"]) - 1])

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

    def test_the_tag_is_said_in_the_reasons_line(self):
        inc = week(JUDAH, linked={"id": "x", "km": 120, "min": 40})
        text = inc["evidence"][-1]["text"]
        self.assertIn("حرارة جديدة مستمرة · عدة مواقع في وقت متقارب", text)
        self.assertIn("ظهرت حرارة جديدة في موقع آخر على بعد 120 كم خلال 40 دقيقة", text)


class InACheck(unittest.TestCase):
    """A whole check keeps what the patterns need when it makes an incident, and the app gets the label."""
    NOW = dt.datetime(2026, 11, 15, 22, 50, tzinfo=UTC)

    def run_check(self, root, noaa_rows, slots=None):
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
        geo.facility.return_value = {"label": "معمل بقيق", "code": "oil", "km": 1.0}
        geo.describe.return_value = ("في السعودية", "السعودية")
        geo.solar_km.return_value = None
        state = {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE, "sea_check": engine.SEA_CHECK,
                 "bootstrapped": iso(self.NOW), "hello_sent": iso(self.NOW), "slots": slots or {}}
        save_json(root / "state" / "state.json", state)
        memory = Memory({})
        memory.record(26.16, 49.75, "2026-11-01", 9.0)    # heat here on 1 Nov, a fortnight before
        memory.record(30, 48, "2026-11-14", 10)
        save_json(root / "state" / "memory.json", memory.cells)
        forbidden = Mock(side_effect=AssertionError("Unexpected source"))
        with patch.multiple(engine, utcnow=Mock(return_value=self.NOW), make_session=Mock(return_value=session),
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

    def test_a_failing_fact_never_stops_the_check(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine.patterns, "usual_pixels", Mock(side_effect=RuntimeError("broken"))):
            S = self.run_check(Path(folder), self.ROWS)
            self.assertEqual(len(S.incidents), 1)


if __name__ == "__main__":
    unittest.main()
