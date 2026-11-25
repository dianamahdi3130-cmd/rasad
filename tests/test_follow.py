"""A posted fire at sea followed after the finders lose it (follow.py).

The case: Hormuz, 24-25 Nov 2026. A fire 22 km north of Khasab was posted at 00:10 Baghdad time and
seen by the sea finder until 03:30. It burned on all day, drifting 7-12 km with the strait's
currents, in every Meteosat-12 picture from 08:10, and no finder saw it: to them it was "hot before".
"""
import copy
import datetime as dt
import unittest
from unittest.mock import patch

import numpy as np

from rasad import engine, follow, patterns, seawatch
from rasad.util import UTC, box_around, iso

SEEN = dt.datetime(2026, 11, 25, 0, 30, tzinfo=UTC)      # the fire's last sighting (03:30 Baghdad)
LAT, LON = 26.3498, 56.2431


def config():
    cfg = copy.deepcopy(engine.DEFAULTS)
    cfg["follow"] = {"enabled": True}
    return cfg


def posted(**extra):
    inc = {"id": "20261124-2030_26.406_56.255", "status": "alerted", "tg_msg": 1325, "place": "sea",
           "lat": 26.406, "lon": 56.2555, "first": "2026-11-24T20:30:00Z", "last": iso(SEEN),
           "observations": [{"t": "2026-11-24T20:30:00Z", "lat": 26.406, "lon": 56.2555},
                            {"t": iso(SEEN), "lat": LAT, "lon": LON}]}
    inc.update(extra)
    return inc


class Sky:
    """Pictures of a calm sea around the last sighting, with a warm speck where the fire is."""

    def __init__(self, cfg, spots):
        self.cfg, self.spots = cfg, spots      # {time: (lat, lon, levels above the sea)}

    def get(self, layer, bbox, W, H, t, channel=None):
        img = np.full((H, W), 12.0, dtype=np.float32)
        spot = self.spots.get(t)
        if spot is not None:
            lat, lon, above = spot
            w, s, e, n = bbox
            r = int((n - lat) / (n - s) * H)
            c = int((lon - w) / (e - w) * W)
            img[r - 1:r + 2, c - 1:c + 2] += above
        return img


def everywhere_sea(bbox, W, H, coast_km, km):
    return np.ones((H, W), dtype=bool)


class Follow(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(seawatch, "sea_grid", everywhere_sea), patch.object(patterns, "_atlas", {})):
            p.start()
            self.addCleanup(p.stop)

    def run_day(self, spots, start, end, inc=None):
        cfg = config()
        inc = inc or posted()
        sky = Sky(cfg, spots)
        replies = []
        t = start
        while t <= end:
            now = t + dt.timedelta(minutes=int(cfg["seawatch"]["delay_min"]))
            follow.run(cfg, None, [inc], now, pics=sky)
            if follow.due(inc, cfg):
                replies.append((t, follow.message(inc, cfg)))
                inc["still_posted"] = iso(now)
            t += dt.timedelta(minutes=10)
        return inc, replies

    def test_the_hormuz_fire_drifting_by_day_is_told_under_its_post_once(self):
        # 08:10-08:40 Baghdad: 7 km west-south-west of the last sighting, 4-11 above the sea
        spots = {dt.datetime(2026, 11, 25, 5, 10 + 10 * k, tzinfo=UTC): (26.335, 56.179 - 0.002 * k, v)
                 for k, v in enumerate([4.5, 5.2, 11.0, 6.7])}
        inc, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                    dt.datetime(2026, 11, 25, 6, 30, tzinfo=UTC))
        self.assertEqual(len(replies), 1, "once")
        when, text = replies[0]
        self.assertEqual(when, dt.datetime(2026, 11, 25, 5, 30, tzinfo=UTC), "from its third picture in a row")
        self.assertIn("🔥 <b>ما زالت الحرارة ظاهرة قرب موضع هذا الحريق</b>", text)
        self.assertIn("في 3 صور متتالية للقمر الأوروبي حتى 08:30 بتوقيت بغداد، على بعد 7 كم غرب "
                      "الموضع الذي رُصد فيه آخر مرة عند 03:30", text)
        self.assertIn("قد تكون النار نفسها وقد انجرفت مع التيار؛ صور الأقمار ترى الحرارة ولا ترى سببها.", text)
        for cause in ("هجوم", "استهداف", "ضربة", "سفينة"):
            self.assertNotIn(cause, text)
        self.assertEqual(inc["follow"]["from"], [iso(SEEN), LAT, LON])

    def test_calm_sea_says_nothing(self):
        inc, replies = self.run_day({}, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                    dt.datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
        self.assertEqual(replies, [])
        self.assertEqual(inc["follow"]["streak"], 0)

    def test_heat_further_than_a_hull_can_drift_is_another_matter(self):
        # 40 minutes after the last sighting: 3 km + 3 km/h x 0.7 h is about 5 km; this is 15 km away
        spots = {dt.datetime(2026, 11, 25, 1, 10 + 10 * k, tzinfo=UTC): (26.35, 56.09, 8.0) for k in range(4)}
        _, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                  dt.datetime(2026, 11, 25, 1, 50, tzinfo=UTC))
        self.assertEqual(replies, [])

    def test_a_speck_running_east_is_a_cloud_or_a_ship_under_way_not_a_drifting_fire(self):
        # the central Gulf, 25 Nov 2026 (no fire): 25.82,53.456 -> 25.848,53.470 -> 25.852,53.501, a
        # speck 3 km further in each picture, about 18 km/h
        seen = dt.datetime(2026, 11, 25, 3, 0, tzinfo=UTC)
        inc = posted(last=iso(seen), observations=[{"t": iso(seen), "lat": 25.9, "lon": 53.3}])
        path = [(25.8203, 53.4560), (25.8484, 53.4697), (25.8518, 53.5009), (25.8600, 53.5300), (25.8650, 53.5600)]
        spots = {dt.datetime(2026, 11, 25, 8, 10 + 10 * k, tzinfo=UTC): (lat, lon, 6.0)
                 for k, (lat, lon) in enumerate(path)}
        _, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 3, 30, tzinfo=UTC),
                                  dt.datetime(2026, 11, 25, 9, 0, tzinfo=UTC), inc)
        self.assertEqual(replies, [])

    def test_a_flicker_is_not_enough(self):
        spots = {dt.datetime(2026, 11, 25, 5, 10, tzinfo=UTC): (26.335, 56.179, 9.0),
                 dt.datetime(2026, 11, 25, 5, 20, tzinfo=UTC): (26.335, 56.179, 9.0),
                 dt.datetime(2026, 11, 25, 5, 50, tzinfo=UTC): (26.335, 56.179, 9.0)}
        _, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                  dt.datetime(2026, 11, 25, 6, 10, tzinfo=UTC))
        self.assertEqual(replies, [], "three pictures in a row, not three in an hour")

    def test_a_usual_flare_nearby_is_a_platform_not_the_fire(self):
        import math
        cell = 0.005
        A = {"cell": cell, "dates": 197, "first": "2024-03-05",
             "cells": {f"{math.floor(26.335 / cell)},{math.floor(56.179 / cell)}": 150}}
        spots = {dt.datetime(2026, 11, 25, 5, 10 + 10 * k, tzinfo=UTC): (26.335, 56.179, 9.0) for k in range(4)}
        with patch.object(patterns, "_atlas", A):
            _, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                      dt.datetime(2026, 11, 25, 6, 0, tzinfo=UTC))
        self.assertEqual(replies, [])

    def test_only_a_posted_fire_at_sea_quiet_for_a_while_and_not_too_long(self):
        cfg = config()
        now = SEEN + dt.timedelta(minutes=45)
        self.assertTrue(follow.wanted(posted(), cfg, now))
        self.assertFalse(follow.wanted(posted(), cfg, SEEN + dt.timedelta(minutes=20)), "still seen by the finders")
        self.assertFalse(follow.wanted(posted(), cfg, SEEN + dt.timedelta(hours=13)), "after 12 hours")
        self.assertFalse(follow.wanted(posted(status="new"), cfg, now), "never posted")
        self.assertFalse(follow.wanted(posted(place="land"), cfg, now))
        self.assertFalse(follow.wanted(posted(still_posted=iso(now)), cfg, now), "once")
        self.assertFalse(follow.wanted(posted(), copy.deepcopy(engine.DEFAULTS), now), "off unless switched on")

    def test_a_missing_picture_waits_for_the_next_check(self):
        cfg = config()
        inc = posted()

        class Nothing:
            def get(self, *a, **k):
                return None
        follow.run(cfg, None, [inc], SEEN + dt.timedelta(hours=2), pics=Nothing())
        self.assertNotIn("follow", inc)


if __name__ == "__main__":
    unittest.main()
