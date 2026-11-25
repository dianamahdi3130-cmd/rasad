"""The great energy sites on land, read by Rasad's own night finder (seawatch boxes with "land").

The case: Ras Tanura, 25 Nov 2026. A blaze at the terminal from 04:40 to 05:10 Baghdad time was in
every Meteosat-12 Fire Temperature picture (a rise of about 100 with a flame's glow) and in none of
the official fire symbols; NASA did not pass. On land a site is warm at night anyway (flares, lamps,
hot roofs): only a clear rise that comes with a flame's glow in the short waves counts there.
"""
import copy
import datetime as dt
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rasad import brain, engine, preliminary, seawatch
from rasad.util import box_around, iso, px_to_latlon

WHEN = dt.datetime(2026, 11, 25, 1, 40, tzinfo=dt.timezone.utc)


def find(rise=100.0, glow=12.0, hot_before=0.0, land=True, everything_land=True):
    cfg = copy.deepcopy(engine.DEFAULTS)
    box = {"id": "site_test", "bbox": box_around(26.628, 50.163, 12.5), "land": land}
    width, height = seawatch.box_size(box, cfg["seawatch"]["km_per_px"])
    row, col = height // 2, width // 2
    lat, lon = px_to_latlon(box["bbox"], width, height, col, row)
    base = np.full((height, width), 30.0, dtype=np.float32)
    before, now = base.copy(), base.copy()
    before[row - 1:row + 2, col - 1:col + 2] += hot_before
    now[row - 1:row + 2, col - 1:col + 2] += hot_before + rise
    dark = np.zeros((height, width), dtype=np.float32)
    shine = dark.copy()
    shine[row - 1:row + 2, col - 1:col + 2] = glow
    pics = Mock()

    def get(layer, bbox, w, h, t, channel=None):
        if channel in (1, 2):
            return shine if t == WHEN else dark
        return now if t == WHEN else before
    pics.get.side_effect = get
    mask = (lambda lats, lons: np.ones_like(np.asarray(lats), dtype=bool)) if everything_land else \
        (lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool))
    with patch.dict(seawatch._SEA, {}, clear=True), \
            patch.object(seawatch, "land_mask_points", return_value=mask), \
            patch.object(seawatch, "sun_elevation", return_value=-30.0), \
            patch.object(seawatch, "_ir105_change", return_value=0.7):
        spots, notes = seawatch.find_spots(cfg, pics, box, WHEN, mem=None, geo=None)
    return spots, notes, (lat, lon)


class Finder(unittest.TestCase):
    def test_a_blaze_with_a_flames_glow_at_a_site_is_found(self):
        spots, notes, (lat, lon) = find()
        self.assertEqual(len(spots), 1, notes)
        self.assertEqual(spots[0]["site"], "site_test")
        self.assertFalse(spots[0]["strong"], "on land a second picture always agrees first")
        self.assertAlmostEqual(spots[0]["lat"], lat, places=2)

    def test_warmth_without_a_flame_is_not_a_blaze(self):
        # a hot roof, a lamp, an exhaust: warmer at 3.8 micron, dark in the short waves
        spots, notes, _ = find(glow=0.0)
        self.assertEqual(spots, [], notes)

    def test_a_small_rise_is_not_enough_on_land(self):
        spots, notes, _ = find(rise=12.0)
        self.assertEqual(spots, [], notes)

    def test_a_flare_that_was_already_burning_is_not_new(self):
        spots, notes, _ = find(hot_before=60.0)
        self.assertEqual(spots, [], notes)
        self.assertIn("hot before (flare)", notes["rejected"])

    def test_the_sea_boxes_still_leave_the_land_out(self):
        spots, notes, _ = find(land=False)
        self.assertEqual(spots, [], notes)

    def test_open_water_in_a_site_box_is_left_to_the_sea_boxes(self):
        spots, notes, _ = find(everything_land=False)
        self.assertEqual(spots, [], notes)


class Switch(unittest.TestCase):
    def test_the_sites_are_read_only_when_switched_on(self):
        C = copy.deepcopy(engine.DEFAULTS["seawatch"])
        self.assertFalse(any(b.get("land") for b in seawatch.live_boxes(C)))
        C["sites"] = True
        ids = [b["id"] for b in seawatch.live_boxes(C)]
        self.assertIn("site_east_sa", ids)
        ras_tanura = next(b for b in C["boxes"] if b["id"] == "site_east_sa")
        w, s, e, n = ras_tanura["bbox"]
        self.assertTrue(w <= 50.163 <= e and s <= 26.628 <= n)
        for box in C["boxes"]:
            if box.get("land"):
                self.assertIs(box.get("day"), False, "the day finder stays at sea")

    def test_a_site_never_makes_a_first_sighting_post(self):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update(enabled=True)
        cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [47.8, 23.5, 56.6, 30.5]}]
        cand = {"t": iso(WHEN), "lat": 26.628, "lon": 50.163, "rise": 98.0, "src": "mtg_spot", "strong": False,
                "flame": True, "site": "site_east_sa", "land": True}
        with patch.object(preliminary, "sun_elevation", return_value=-30.0):
            self.assertEqual(preliminary.public_candidates(cfg, {"sea_candidates": [cand]}, [], WHEN), [])


class Posting(unittest.TestCase):
    def incident(self, pictures):
        cfg = copy.deepcopy(engine.DEFAULTS)
        dets = [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(WHEN + dt.timedelta(minutes=10 * k)),
                 "lat": 26.628, "lon": 50.163, "site": "site_east_sa"} for k in range(pictures)]
        inc = brain.new_incident(dets[0], "new")
        for d in dets:
            brain.add_to_incident(inc, d)
        # 3 km from the mapped port, 9 km from the refinery, heat around on most days (its flares)
        inc.update(place="land", where="في السعودية", country="السعودية", regions=["gulf"],
                   region_name="الخليج الفارسي", known_days=30, area_days=30, typical_frp=6.9,
                   facility={"label": "ميناء رأس تنورة", "code": "port", "km": 3.2})
        return brain.finalize(inc, cfg), cfg

    def test_a_blaze_seen_in_two_pictures_at_a_site_is_news(self):
        inc, cfg = self.incident(2)
        self.assertEqual(inc["verdict"], "مرجّح")
        self.assertTrue(brain.site_blaze(inc))
        self.assertEqual(inc["priority"], "high")
        self.assertTrue(brain.alert_worthy(inc, cfg))

    def test_one_picture_waits(self):
        inc, cfg = self.incident(1)
        self.assertEqual(inc["priority"], "low")

    def test_rasads_finder_alone_is_never_more_than_likely(self):
        inc, _ = self.incident(4)
        self.assertEqual(inc["verdict"], "مرجّح")
        for cause in ("هجوم", "استهداف", "ضربة"):
            self.assertNotIn(cause, brain.alert_message(inc, copy.deepcopy(engine.DEFAULTS)))


if __name__ == "__main__":
    unittest.main()
