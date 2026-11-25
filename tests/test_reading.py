"""«قراءة رصد»: Rasad's reading of a posted event once it has run its course (rasad/reading.py).
Pinned on the real events of 23-25 Nov 2026: the Shaybah heat (short, strong from the start, no
smoke: more like an emergency gas flare), the Hormuz fires at sea (how far the heat moved) and the
heat on the Oman pipeline (two stretches, at a spot NASA never saw hot)."""
import copy
import datetime as dt
import unittest

from rasad import brain, engine, reading
from rasad.util import UTC, iso

ATLAS = {"cell": 0.005, "dates": 197, "first": "2024-03-05", "cells": {}}


def cfg():
    c = copy.deepcopy(engine.DEFAULTS)
    c["reading"] = {"enabled": True}       # on in config.yaml, off in DEFAULTS
    return c


def geo(t, lat, lon, frp=None, src="mtg_frp"):
    d = {"src": src, "family": "mtg", "kind": "geo", "t": t, "lat": lat, "lon": lon}
    if frp is not None:
        d["frp"] = frp
    return d


def incident(dets, **fields):
    inc = brain.new_incident(dets[0], fields.pop("kind", "new"))
    for d in dets:
        brain.add_to_incident(inc, d)
    inc.update(status="alerted", tg_msg=7, alerted_verdict="مرجّح", **fields)
    return inc


def shaybah():
    rows = [geo("2026-11-25T12:10:00Z", 22.6769, 54.1375, 51.31), geo("2026-11-25T12:20:00Z", 22.6681, 54.1304, 57.45),
            geo("2026-11-25T12:30:00Z", 22.6769, 54.1375, 42.1)]
    return incident(rows, kind="unusual", place="land", typical_frp=2.6, site_habit=9.8, known_days=28,
                    facility={"label": "حقل شيبة", "code": "refinery", "km": 1.1},
                    smoke={"seen": False, "checked": 3, "why": "no streak at the heat"})


class Readings(unittest.TestCase):
    def test_shaybah_reads_like_an_emergency_gas_flare(self):
        inc = shaybah()
        now = dt.datetime(2026, 11, 25, 13, 45, tzinfo=UTC)
        out = reading.make(inc, cfg(), now, atlas=ATLAS)
        text = "\n".join(out)
        self.assertIn("⏱ المدة: نحو 30 دقيقة (15:10 – 15:30 بتوقيت بغداد)، في 3 صور للقمر الأوروبي، ثم لم تعد تظهر", text)
        self.assertIn("بلغت ذروتها 58 MW", text)   # as the posts round it (57.5)
        self.assertIn("(بدأت قوية ثم خفّت)", text)
        self.assertIn("💨 الدخان: لم يظهر دخان في صور النهار (3 صور)", text)
        self.assertIn("لم تظهر حرارة في هذه النقطة في سجل ناسا", text)
        self.assertIn("⚖️ الترجيح: يشبه حرقاً طارئاً للغاز في المنشأة أكثر مما يشبه حريقاً", text)
        self.assertTrue(out[-1].startswith("ℹ️"))

    def test_nothing_is_read_while_the_event_burns_on_or_has_one_sighting(self):
        inc = shaybah()
        self.assertIsNone(reading.make(inc, cfg(), dt.datetime(2026, 11, 25, 12, 50, tzinfo=UTC), atlas=ATLAS))
        one = incident([{"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-25T10:02:00Z",
                         "lat": 26.2893, "lon": 56.1398, "frp": 8.9, "night": False}], place="sea")
        self.assertIsNone(reading.make(one, cfg(), dt.datetime(2026, 11, 25, 14, tzinfo=UTC), atlas=ATLAS))

    def test_a_fire_with_smoke_or_burning_for_hours_reads_like_a_fire(self):
        inc = shaybah()
        inc["smoke"] = {"seen": True, "checked": 3}
        out = reading.make(inc, cfg(), dt.datetime(2026, 11, 25, 13, 45, tzinfo=UTC), atlas=ATLAS)
        self.assertIn("⚖️ الترجيح: يشبه حريقاً أكثر مما يشبه حرق غاز (ظهر دخان)", out)
        rows = [geo(iso(dt.datetime(2026, 11, 25, 6, tzinfo=UTC) + dt.timedelta(minutes=20 * k)), 26.16, 49.76, 20 + k)
                for k in range(13)]
        long_ = incident(rows, place="land", facility={"label": "معمل بقيق", "code": "oil", "km": 1.0})
        out = reading.make(long_, cfg(), dt.datetime(2026, 11, 25, 12, 30, tzinfo=UTC), atlas=ATLAS)
        self.assertIn("⚖️ الترجيح: يشبه حريقاً أكثر مما يشبه حرق غاز (استمر ساعات)", out)

    def test_at_sea_the_heat_that_moved_and_the_dawn_are_said(self):
        track = [("2026-11-23T22:10:00Z", 26.3431, 56.2901), ("2026-11-24T00:00:00Z", 26.3656, 56.2728),
                 ("2026-11-24T02:00:00Z", 26.415, 56.2233)]
        inc = incident([geo(t, la, lo, src="mtg_spot") for t, la, lo in track], place="sea")
        out = reading.make(inc, cfg(), dt.datetime(2026, 11, 24, 4, tzinfo=UTC), atlas=ATLAS)
        text = "\n".join(out)
        self.assertIn("تحرّك موضع الحرارة نحو 10 كم باتجاه", text)
        self.assertIn("بعد الفجر لا تظهر الحرارة الصغيرة في البحر", text)
        self.assertNotIn("⚖️", text, "at sea the reading says what was measured, not what it looks like")

    def test_two_stretches_hours_apart_are_said_as_two(self):
        rows = [geo(t, 18.45, 55.24, 10) for t in ("2026-11-24T21:10:00Z", "2026-11-24T22:10:00Z", "2026-11-24T23:30:00Z")]
        rows.append(geo("2026-11-25T09:03:00Z", 18.45, 55.24, 18))
        inc = incident(rows, place="land", known_days=0, area_days=0, facility=None)
        out = reading.make(inc, cfg(), dt.datetime(2026, 11, 25, 11, tzinfo=UTC), atlas=ATLAS)
        self.assertIn("على فترتين", out[0])
        self.assertIn("لم تُرصد حرارة هنا ولا حوله خلال شهر، ولا منشأة مسجّلة في الخريطة", "\n".join(out))

    def test_no_reading_ever_names_a_cause(self):
        inc = shaybah()
        out = reading.make(inc, cfg(), dt.datetime(2026, 11, 25, 13, 45, tzinfo=UTC), atlas=ATLAS)
        msg = reading.message(out)
        for word in reading.FORBIDDEN:
            self.assertNotIn(word, msg)
        self.assertTrue(msg.startswith("📋 <b>قراءة رصد للحدث</b>"))

    def test_posted_once_while_it_is_news(self):
        inc = shaybah()
        now = dt.datetime(2026, 11, 25, 13, 45, tzinfo=UTC)
        self.assertTrue(reading.due(inc, cfg(), now))
        inc["reading_posted"] = iso(now)
        self.assertFalse(reading.due(inc, cfg(), now))
        old = shaybah()
        self.assertFalse(reading.due(old, cfg(), now + dt.timedelta(hours=13)))
        quiet = cfg()
        quiet["reading"]["post"] = False
        self.assertFalse(reading.due(shaybah(), quiet, now))


if __name__ == "__main__":
    unittest.main()
