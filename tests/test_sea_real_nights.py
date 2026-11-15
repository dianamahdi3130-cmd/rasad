"""The night sea finder on real pictures (small cuts kept from Meteosat-12's Fire Temperature
picture at full detail, 0.25 km per pixel, read on 15 November 2026 through the probe workflow).

Three real events must be found, and two nights that used to give false first alerts must stay
silent. The numbers in engine.DEFAULTS["seawatch"] were tuned on these very nights (whole sea
boxes); these cuts keep that result from slipping. See
docs/reviews/hormuz_ras_al_khaimah_20261114.md.
"""
import copy
import datetime as dt
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from rasad import engine, seawatch
from rasad.util import UTC, haversine_km, iso

FIXTURES = Path(__file__).parent / "fixtures"


class Night:
    """One kept cut: answers the finder's picture requests from the stored colours."""

    def __init__(self, name):
        data = np.load(FIXTURES / f"{name}.npz")
        self.bbox = [float(v) for v in data["bbox"]]
        self.frames = {dt.datetime.strptime(key[1:], "%Y%m%dT%H%M").replace(tzinfo=UTC): data[key]
                       for key in data.files if key.startswith("t")}
        self.height, self.width = next(iter(self.frames.values())).shape[:2]
        self.cache = {}

    def get(self, layer, bbox, width, height, when, channel=None):
        frame = self.frames.get(when)
        if layer != "mtg_fd:rgb_firetemperature" or frame is None or (width, height) != (self.width, self.height):
            return None   # the 10.5 micron check picture was not kept: that test is skipped
        rgb = frame.astype(np.float32)
        return rgb.mean(axis=2) if channel is None else rgb[..., channel]

    def run(self, overrides=None):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"].update(overrides or {})
        box = {"id": "cut", "bbox": self.bbox}
        found, rejected = {}, {}
        with patch.object(seawatch, "box_size", return_value=(self.width, self.height)):
            for when in sorted(self.frames):
                refs = [m for m in cfg["seawatch"]["ref_minutes"]
                        if when - dt.timedelta(minutes=m) in self.frames]
                if not refs:
                    continue
                spots, notes = seawatch.find_spots(cfg, self, box, when, refs=refs)
                assert spots is not None, notes
                for why, count in notes["rejected"].items():
                    rejected[why] = rejected.get(why, 0) + count
                if spots:
                    found[when.strftime("%H:%M")] = spots
        return found, rejected


def near(spot, lat, lon, km=4.0):
    return haversine_km(spot["lat"], spot["lon"], lat, lon) <= km


class RealNights(unittest.TestCase):
    def test_fire_off_ras_al_khaimah_14_november_is_found_in_consecutive_pictures(self):
        # A channel showed this fire at 16:35 UTC; Rasad had read it blurred and saw nothing.
        found, _ = Night("sea_fire_20261114").run()
        times = sorted(found)
        self.assertGreaterEqual(len(times), 3, found)
        self.assertIn(times[0], ("15:40", "15:50"))      # its first two pictures
        self.assertLessEqual(times[-1], "16:20")         # gone in the picture of 16:30
        self.assertIn("16:10", times)
        for spots in found.values():
            for spot in spots:                           # its footprint may split in two specks
                self.assertTrue(near(spot, 26.0225, 55.9012, km=5.0), spot)
                self.assertTrue(spot["flame"])           # a glow in the short-wave colours
                self.assertLessEqual(spot["restless"], 0.05)
                self.assertFalse(spot["strong"])         # faint: told after the second picture, not the first
        pair = seawatch.confirm([dict(spot, src=seawatch.SRC) for spots in found.values() for spot in spots],
                                found["16:10"][0])
        self.assertTrue(pair, "two pictures within 40 minutes at the same place make it likely")

    def test_the_blurred_rule_set_would_have_missed_it(self):
        # the old margin (18 levels of red, no flame rule) sees nothing even at full detail
        found, _ = Night("sea_fire_20261114").run({"min_rise": 18.0, "flame_glow": 0})
        self.assertEqual(found, {})

    def test_known_ship_fire_of_1_november_is_found_from_its_first_picture(self):
        found, _ = Night("sea_fire_20261101").run()
        self.assertEqual(sorted(found), ["21:50", "22:00", "22:10", "22:30"])
        for spots in found.values():
            self.assertTrue(all(near(spot, 26.0129, 56.54) for spot in spots), spots)
        self.assertTrue(found["22:10"][0]["strong"])     # a rise of 18 with a glow of 6

    def test_one_very_strong_picture_is_told_at_once(self):
        found, _ = Night("sea_flash_20261113").run()
        self.assertIn("18:40", found)
        spot = found["18:40"][0]
        self.assertTrue(near(spot, 26.3723, 56.2975))
        self.assertTrue(spot["strong"] and spot["flame"])
        self.assertGreater(spot["rise"], 80)
        self.assertGreater(spot["glow"], 30)
        self.assertNotIn("18:30", found)

    def test_flickering_halo_of_the_das_island_flare_is_not_a_fire(self):
        # four "first thermal sighting" posts went out for this flare on 14-15 November
        found, rejected = Night("sea_flare_das_20261114").run()
        self.assertEqual(found, {})
        self.assertTrue(rejected, "the halo does produce candidates; every one is turned down")

    def test_restless_cloudy_night_gives_no_spot(self):
        found, rejected = Night("sea_clouds_20261111").run()
        self.assertEqual(found, {})
        self.assertGreater(sum(rejected.values()), 20)


class Measures(unittest.TestCase):
    def test_restless_share_separates_calm_sea_from_drifting_cloud(self):
        rng = np.random.default_rng(7)
        sea = np.ones((161, 161), bool)
        calm = np.zeros((161, 161), np.float32)
        calm[80, 80] = 20
        self.assertEqual(seawatch.restless_share(calm, sea, 80, 80, 0.25), 0.0)
        cloudy = rng.normal(0, 4, (161, 161)).astype(np.float32)
        self.assertGreater(seawatch.restless_share(cloudy, sea, 80, 80, 0.25), 0.3)
        self.assertEqual(seawatch.restless_share(calm, np.zeros_like(sea), 80, 80, 0.25), 1.0)

    def test_a_spot_beside_a_much_stronger_source_is_its_halo(self):
        sea = np.ones((121, 121), bool)
        cur = np.full((121, 121), 20, np.float32)
        ref = cur.copy()
        cur[60, 60] = 30                      # the candidate
        self.assertFalse(seawatch.stronger_source_near(cur, ref, sea, 60, 60, 0.25))
        cur[60, 80] = 200                     # a flare 5 km away
        self.assertTrue(seawatch.stronger_source_near(cur, ref, sea, 60, 60, 0.25))
        cur[60, 80] = 20
        ref[60, 80] = 180                     # it was blazing before, and has faded now
        self.assertTrue(seawatch.stronger_source_near(cur, ref, sea, 60, 60, 0.25))

    def test_baseline_ignores_one_odd_picture_of_three(self):
        calm = np.full((4, 4), 21, np.float32)
        fire = calm.copy()
        fire[1, 1] = 40
        self.assertEqual(float(seawatch._baseline([fire, calm, calm])[1, 1]), 21.0)   # the fire stays new
        self.assertEqual(float(seawatch._baseline([fire, calm])[1, 1]), 40.0)         # two: the warmest


class Tiles(unittest.TestCase):
    def test_small_picture_is_one_request(self):
        parts = seawatch.tile_requests("layer", [55, 25, 56, 26], 400, 444, "t")
        self.assertEqual(parts, [(("layer", (55, 25, 56, 26), 400, 444, "t"), (0, 0))])

    def test_big_picture_is_cut_pixel_exactly(self):
        width, height = 2082, 2136
        parts = seawatch.tile_requests("layer", [55.0, 22.4, 60.2, 27.2], width, height, "t")
        self.assertEqual(len(parts), 4)
        covered = np.zeros((height, width), int)
        for (_, box, w, h, _), (x0, y0) in parts:
            self.assertLessEqual(max(w, h), seawatch.TILE_PX)
            covered[y0:y0 + h, x0:x0 + w] += 1
            # the tile's box is exactly its pixels' share of the whole box
            self.assertAlmostEqual((box[0] - 55.0) / 5.2 * width, x0, places=6)
            self.assertAlmostEqual((27.2 - box[3]) / 4.8 * height, y0, places=6)
        self.assertTrue((covered == 1).all())

    def test_tiles_are_fetched_and_put_together(self):
        width, height = 1500, 1300
        bbox = [50.0, 20.0, 53.75, 23.25]
        whole = np.zeros((height, width, 4), np.uint8)
        whole[..., 0] = (np.arange(width)[None, :] % 251).astype(np.uint8)
        whole[..., 1] = (np.arange(height)[:, None] % 241).astype(np.uint8)
        whole[..., 3] = 255
        when = dt.datetime(2026, 11, 14, 16, 10, tzinfo=UTC)
        asked = []

        class Server:
            url = "test"

            def getmap(self, layer, box, w, h, t=None, quiet=False, **_):
                from PIL import Image
                asked.append((w, h))
                x0 = round((box[0] - bbox[0]) / (bbox[2] - bbox[0]) * width)
                y0 = round((bbox[3] - box[3]) / (bbox[3] - bbox[1]) * height)
                return Image.fromarray(whole[y0:y0 + h, x0:x0 + w]), None

        pics = seawatch.Pictures(Server())
        red = pics.get("layer", bbox, width, height, when, 0)
        self.assertEqual(sorted(asked), [(750, 650)] * 4)
        np.testing.assert_array_equal(red, whole[..., 0].astype(np.float32))
        np.testing.assert_array_equal(pics.get("layer", bbox, width, height, when, 1),
                                      whole[..., 1].astype(np.float32))
        self.assertEqual(len(asked), 4, "the second colour comes from the same download")
        raw = pics.cache[("layer", tuple(bbox), width, height, iso(when), "rgba")]
        np.testing.assert_array_equal(raw, whole)

    def test_a_missing_tile_leaves_the_picture_not_ready(self):
        class Server:
            url = "test"
            calls = 0

            def getmap(self, layer, box, w, h, t=None, quiet=False, **_):
                from PIL import Image
                Server.calls += 1
                if Server.calls == 2:
                    return None, None
                return Image.fromarray(np.full((h, w, 4), 255, np.uint8)), None

        pics = seawatch.Pictures(Server(), attempts=1)
        when = dt.datetime(2026, 11, 14, 16, 10, tzinfo=UTC)
        self.assertIsNone(pics.get("layer", [50.0, 20.0, 53.75, 23.25], 1500, 1300, when, 0))


if __name__ == "__main__":
    unittest.main()
