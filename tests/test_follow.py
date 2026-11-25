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

    def __init__(self, cfg, spots, cloud=False):
        self.cfg, self.spots = cfg, spots      # {time: (lat, lon, levels above the sea)}
        self.cloud = cloud                     # a sunlit cloud: as bright in the short waves as at 3.8 micron

    def get(self, layer, bbox, W, H, t, channel=None):
        img = np.full((H, W), 12.0, dtype=np.float32)
        spot = self.spots.get(t)
        if spot is not None and (channel in (None, 0) or self.cloud):
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

    def run_day(self, spots, start, end, inc=None, cloud=False):
        cfg = config()
        inc = inc or posted()
        sky = Sky(cfg, spots, cloud)
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

    def test_a_sunlit_cloud_or_glint_that_stays_is_not_heat(self):
        # four fireless seas on 22 and 25 Nov 2026: specks staying for an hour or more after sunrise,
        # as bright at 1.6 and 2.2 micron as at 3.8 micron (the fire: red far ahead of both)
        spots = {dt.datetime(2026, 11, 25, 3, 0, tzinfo=UTC) + dt.timedelta(minutes=30 + 10 * k): (26.355, 56.30, 9.0)
                 for k in range(6)}
        _, replies = self.run_day(spots, dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
                                  dt.datetime(2026, 11, 25, 5, 0, tzinfo=UTC), cloud=True)
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
        self.assertTrue(follow.wanted(posted(still_posted=iso(now)), cfg, now),
                        "followed on after its reply: where it is now tells whether a later sighting is this fire")
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


class PostedFireNearby(unittest.TestCase):
    """Hormuz, 25 Nov 2026: a fire posted at 18:01 Baghdad time was still in every picture, 4 km away,
    when a spot beside it was posted at 20:10 as a new fire, with only «قبل 17 ساعة رُصد حريق مؤكد آخر…».
    The owner asked whether it was new."""

    def posted_fire(self):
        from rasad.brain import add_to_incident, finalize, new_incident
        times = [f"2026-11-25T{h}:{m}:00Z" for h in ("14", "15", "16", "17") for m in ("00", "20", "40")]
        path = [(26.3161 + 0.0025 * k, 56.2629 + 0.004 * k) for k in range(len(times))]
        dets = [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": la, "lon": lo}
                for t, (la, lo) in zip(times, path)]
        inc = new_incident(dets[0], "new")
        for d in dets:
            add_to_incident(inc, d)
        inc.update(place="sea", status="alerted", tg_msg=1342, alerted_at="2026-11-25T15:01:38Z")
        return finalize(inc, config())

    def new_spot(self, t="2026-11-25T17:10:00Z", lat=26.3431, lon=56.2629):
        from rasad.brain import add_to_incident, finalize, new_incident
        d = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": lat, "lon": lon}
        inc = new_incident(d, "new")
        add_to_incident(inc, d)
        later = iso(dt.datetime.fromisoformat(t.replace("Z", "+00:00")) + dt.timedelta(minutes=40))
        add_to_incident(inc, dict(d, t=later, lat=lat + 0.0157, lon=lon + 0.0124))
        inc.update(place="sea", where="في البحر، على بعد 18 كم شمال خصب (عُمان)", country="عُمان",
                   regions=["hormuz"], region_name="مضيق هرمز", known_days=0, area_days=0)
        return finalize(inc, config())

    def test_a_spot_beside_a_posted_fire_still_burning_says_so(self):
        from rasad import brain
        old, new = self.posted_fire(), self.new_spot()
        near = brain.near_posted(new, [old, new])
        self.assertTrue(near["still"])
        self.assertLessEqual(near["km"], 5)
        new["near_posted"] = near
        text = brain.alert_message(new, config())
        self.assertIn("📌 على بعد 4 كم من حريق في البحر نُشر عند 18:01 وما زال ظاهراً في الصور", text)
        self.assertNotIn("حريق مؤكد آخر", text)

    def test_a_spot_where_a_posted_fire_drifted_since_says_it_may_be_the_same(self):
        from rasad import brain
        old = self.posted_fire()
        new = self.new_spot(t="2026-11-25T19:40:00Z", lat=26.36, lon=56.33)    # 1h50 after its last sighting
        near = brain.near_posted(new, [old, new])
        self.assertFalse(near["still"])
        self.assertIn("قد تكون النار نفسها وقد انجرفت", brain.near_posted_line(near, config()))

    def test_far_or_long_ago_is_another_matter(self):
        from rasad import brain
        old = self.posted_fire()
        self.assertIsNone(brain.near_posted(self.new_spot(lat=26.0, lon=56.6), [old]))
        self.assertIsNone(brain.near_posted(self.new_spot(t="2026-11-26T08:00:00Z"), [old]))
        old["status"] = "new"
        self.assertIsNone(brain.near_posted(self.new_spot(), [old]), "only a posted fire")


class OneFireFollowedIsOneFire(unittest.TestCase):
    """Hormuz, 23-25 Nov 2026: one source was in every picture for 47 hours, drifting within 20 km, and
    was posted six times as a new fire. A sighting where a posted fire's heat was followed to in the
    last hour is that fire (brain._followed_km)."""

    RULES = {"merge_km": 6, "merge_hours": 12}

    def fire(self, track):
        inc = posted()
        inc["follow"] = {"track": track, "streak": len(track), "looked": track[-1][0]}
        return inc

    def find(self, inc, det):
        from rasad.brain import find_incident
        return find_incident([inc], det, 6, 12, rules={})

    def test_nasas_day_pass_on_the_followed_heat_continues_the_fire(self):
        # the night fire's own last sighting 00:30 UTC; followed from 05:30; NASA at 10:02, 13 km away
        inc = self.fire([[f"2026-11-25T{h:02d}:{m:02d}:00Z", 26.2936, 56.1502, 6.0] for h, m in ((9, 40), (9, 50), (10, 0))])
        nasa = {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-25T10:02:00Z",
                "lat": 26.2897, "lon": 56.1407, "frp": 8.9, "conf": "n"}
        self.assertIs(self.find(inc, nasa), inc)

    def test_the_night_finder_regaining_it_a_day_later_continues_it(self):
        inc = self.fire([["2026-11-25T13:30:00Z", 26.3107, 56.2138, 8.9], ["2026-11-25T13:50:00Z", 26.3152, 56.2388, 7.0]])
        spot = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": "2026-11-25T14:40:00Z", "lat": 26.3161, "lon": 56.2629}
        self.assertIs(self.find(inc, spot), inc, "14 hours after its own last sighting, 5 km from where it began")

    def test_heat_elsewhere_or_after_the_trail_went_cold_is_another_fire(self):
        inc = self.fire([["2026-11-25T10:00:00Z", 26.2936, 56.1502, 6.0]])
        far = {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-25T10:02:00Z",
               "lat": 26.36, "lon": 56.26, "frp": 8.9, "conf": "n"}
        self.assertIsNone(self.find(inc, far), "13 km from where the heat was followed to")
        later = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": "2026-11-25T14:40:00Z", "lat": 26.2936, "lon": 56.1502}
        self.assertIsNone(self.find(inc, later), "not followed there for over an hour")

    def test_only_a_posted_fire_at_sea_is_continued_so(self):
        track = [["2026-11-25T10:00:00Z", 26.2936, 56.1502, 6.0]]
        nasa = {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-25T10:02:00Z",
                "lat": 26.2897, "lon": 56.1407, "frp": 8.9, "conf": "n"}
        for change in ({"status": "new"}, {"place": "land"}):
            inc = self.fire(track)
            inc.update(change)
            self.assertIsNone(self.find(inc, nasa), change)

    def test_it_is_followed_on_after_its_reply_and_not_past_five_days(self):
        cfg = config()
        now = SEEN + dt.timedelta(minutes=45)
        self.assertTrue(follow.wanted(posted(still_posted=iso(now)), cfg, now), "where it is now is still asked")
        self.assertFalse(follow.due(dict(posted(still_posted=iso(now)), follow={"streak": 5, "track": []}), cfg),
                         "but the reply goes out once")
        old = posted(first="2026-11-19T00:00:00Z")
        self.assertFalse(follow.wanted(old, cfg, now))

    def test_a_first_sighting_on_the_followed_heat_is_not_a_first_sighting(self):
        from rasad import preliminary
        inc = self.fire([["2026-11-25T16:00:00Z", 26.33, 56.275, 9.0]])
        when = dt.datetime(2026, 11, 25, 16, 10, tzinfo=UTC)
        self.assertTrue(preliminary._represented(26.335, 56.29, when, [inc]))
        self.assertFalse(preliminary._represented(26.45, 56.45, when, [inc]))
