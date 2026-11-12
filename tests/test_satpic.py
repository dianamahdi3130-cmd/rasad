"""The before/after satellite picture under an alert: the Sentinel-2 reader (rasad/s2.py) and the
queue, picture and channel post (rasad/satpic.py), against a made-up archive. No network."""
import copy
import datetime as dt
import json
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from rasad import engine, s2, satpic
from rasad.brain import Memory, video_note
from rasad.util import UTC, iso, load_json, parse_time, save_json

LAT, LON, ZONE = 26.0, 50.0, 39
BASE = {"blue": 0.20, "green": 0.25, "red": 0.30, "nir08": 0.35, "swir16": 0.40, "swir22": 0.38}
FILES = {"B02": "blue", "B03": "green", "B04": "red", "B8A": "nir08", "B11": "swir16", "B12": "swir22", "SCL": "scl"}
EC, NC = (float(v) for v in s2.utm_forward(LAT, LON, ZONE))
X0, Y0 = round((EC - 2560) / 20) * 20, round((NC + 2560) / 20) * 20


def make_cog(image, tile, res, x0, y0, levels=2):
    """A tiled, zlib-compressed TIFF with the row predictor and overviews, as in the real archive."""
    bits = image.dtype.itemsize * 8
    plans = []
    for k in range(levels):
        img = image[::1 << k, ::1 << k]
        w = img.shape[0]
        across = -(-w // tile)
        padded = np.zeros((across * tile, across * tile), image.dtype)
        padded[:w, :w] = img
        blocks = []
        for ty in range(across):
            for tx in range(across):
                block = padded[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
                diff = np.diff(block, axis=1, prepend=block.dtype.type(0)).astype(block.dtype)
                blocks.append(zlib.compress(diff.astype("<u2" if bits == 16 else np.uint8).tobytes()) if block.any() else b"")
        plans.append((w, blocks))
    entries = lambda k: 15 + (1 if k == 0 else 0)                                   # noqa: E731
    dir_size = lambda k: 2 + entries(k) * 12 + 4 + (len(plans[k][1]) * 8 if len(plans[k][1]) > 1 else 0) + (72 if k == 0 else 0)  # noqa: E731
    pos, dir_at, tile_at = 8, [], []
    for k in range(levels):
        dir_at.append(pos)
        pos += dir_size(k)
    for w, blocks in plans:
        at = []
        for b in blocks:
            at.append(pos)
            pos += len(b)
        tile_at.append(at)
    out = bytearray(pos)
    out[:8] = b"II" + struct.pack("<HI", 42, dir_at[0])
    for k, (w, blocks) in enumerate(plans):
        at, extra = dir_at[k], dir_at[k] + 2 + entries(k) * 12 + 4
        struct.pack_into("<H", out, at, entries(k))
        at += 2

        def entry(tag, kind, count, value):
            nonlocal at
            struct.pack_into("<HHI", out, at, tag, kind, count)
            struct.pack_into("<H" if kind == 3 and count == 1 else "<I", out, at + 8, value)
            at += 12

        def longs(tag, values):
            nonlocal extra
            if len(values) == 1:
                return entry(tag, 4, 1, values[0])
            entry(tag, 4, len(values), extra)
            for v in values:
                struct.pack_into("<I", out, extra, v)
                extra += 4

        for tag, value in ((254, 1 if k else 0), (256, w), (257, w)):
            entry(tag, 4, 1, value)
        for tag, value in ((258, bits), (259, 8), (262, 1), (277, 1), (284, 1), (317, 2), (322, tile), (323, tile)):
            entry(tag, 3, 1, value)
        longs(324, [tile_at[k][i] if b else 0 for i, b in enumerate(blocks)])
        longs(325, [len(b) for b in blocks])
        entry(339, 3, 1, 1)
        if k == 0:
            entry(33550, 12, 3, extra)
            struct.pack_into("<3d", out, extra, res, res, 0)
            extra += 24
            entry(33922, 12, 6, extra)
            struct.pack_into("<6d", out, extra, 0, 0, 0, x0, y0, 0)
        else:
            entry(42113, 2, 1, 48)
        struct.pack_into("<I", out, at, dir_at[k + 1] if k + 1 < levels else 0)
        for i, b in enumerate(blocks):
            out[tile_at[k][i]:tile_at[k][i] + len(b)] = b
    return bytes(out)


class FakeArchive:
    """A few passes over one place; ``request`` stands in for requests.Session."""

    def __init__(self, passes):
        self.passes, self.calls, self.fail, self.files = [], [], None, {}
        for p in passes:
            p = dict(p)
            p.setdefault("platform", "sentinel-2b")
            p["id"] = f"S2B_T39RXX_{p['date'].replace('-', '')}T072146_L2A"
            p["datetime"] = p["date"] + "T07:22:30.000000Z"
            p["dir"] = f"{s2.C1_FILES}39/R/XX/{p['date'][:4]}/{int(p['date'][5:7])}/{p['id']}/"
            self.passes.append(p)

    def ground(self, p, band, E, N):
        west = (E < EC) & np.ones(np.broadcast(E, N).shape, bool)
        cloud = west | True if p.get("cloud") == "all" else west & (p.get("cloud") == "west")
        if band == "scl":
            return np.where(cloud, 9, 5).astype(np.uint8)
        v = BASE[band] + 2e-5 * (E - EC) + 1e-5 * (N - NC)
        if p.get("burn"):
            v = np.where((np.abs(E - EC - 300) < 50) & (np.abs(N - NC - 200) < 50), v * 0.4, v)
        if p.get("flame"):
            hot = (np.abs(E - EC + 400) < 20) & (np.abs(N - NC + 400) < 20)
            v = np.where(hot, {"swir22": 1.2, "swir16": 0.6}.get(band, 0.3), v)
        v = np.where(cloud, 0.8, v)
        return np.round((v + 0.1) / 1e-4).astype(np.uint16)

    def file(self, url):
        if url not in self.files:
            p = next((q for q in self.passes if url.startswith(q["dir"])), None)
            band = p and FILES.get(url[len(p["dir"]):].replace(".tif", ""))
            data = None
            if band:
                res = 10 if band in ("blue", "green", "red") else 20
                n = 5120 // res
                i = np.arange(n) + 0.5
                data = make_cog(self.ground(p, band, (X0 + i * res)[None, :], (Y0 - i * res)[:, None] + 0 * i[None, :]), 256, res, X0, Y0)
            self.files[url] = data
        return self.files[url]

    def request(self, method, url, timeout=None, headers=None, json=None):
        self.calls.append((method, url, (headers or {}).get("Range")))
        assert timeout and timeout > 0, "every request carries a time limit"
        how = self.fail(url) if self.fail else None
        if how == "down":
            raise __import__("requests").ConnectionError("down")
        if how == "slow":
            raise __import__("requests").Timeout("slow")
        answer = Mock()
        if isinstance(how, int):
            answer.status_code, answer.content = how, b"no"
            return answer
        if url.endswith("/search"):
            start, end = (parse_time(t) for t in json["datetime"].split("/"))
            feats = []
            for p in self.passes:
                if start <= parse_time(p["datetime"]) <= end:
                    ring = [[lo, la] for la, lo in (self.corner(dx, dy) for dx, dy in ((-2560, 2560), (2560, 2560), (2560, -2560), (-2560, -2560), (-2560, 2560)))]
                    feats.append({"id": p["id"], "collection": "sentinel-2-c1-l2a", "geometry": {"type": "Polygon", "coordinates": [ring]},
                                  "properties": {"datetime": p["datetime"], "platform": p["platform"], "eo:cloud_cover": 1,
                                                 "grid:code": "MGRS-39RXX", "proj:code": "EPSG:32639"}})
            body = {"type": "FeatureCollection", "features": feats, "links": []}
            answer.status_code, answer.content, answer.json = 200, b"{}", lambda: body
            return answer
        data = self.file(url)
        if data is None:
            answer.status_code, answer.content = 404, b"no"
            return answer
        if how == "norange":
            answer.status_code, answer.content = 200, data
            return answer
        a, b = (int(v) for v in headers["Range"].replace("bytes=", "").split("-"))
        answer.status_code, answer.content = 206, data[a:b + 1]
        return answer

    @staticmethod
    def corner(dx, dy):
        # inverse of the forward projection by two Newton steps: plenty for a footprint corner
        lat, lon = LAT, LON
        for _ in range(3):
            e, n = s2.utm_forward(lat, lon, ZONE)
            lat += (NC + dy - float(n)) / 110600.0
            lon += (EC + dx - float(e)) / (111320.0 * np.cos(np.radians(lat)))
        return lat, lon


PASSES = [{"date": "2026-10-05"}, {"date": "2026-10-25", "cloud": "all"}, {"date": "2026-11-02", "cloud": "all"},
          {"date": "2026-11-09", "burn": True, "flame": True}]
T = lambda s: parse_time(s)                                                        # noqa: E731


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeArchive(PASSES)
        self.archive = s2.Archive(self.fake)
        self.grid = s2.Grid(LAT, LON, 1)

    def test_utm_matches_reference_values_at_the_zone_edges(self):
        for lat, lon, zone, e, n in ((26.5, 42.0, 37, 799033.456, 2934552.841), (30.0, 48.0, 39, 210590.347, 3322575.904),
                                     (26.0, 54.0, 40, 199681.925, 2879132.534), (25.3, 60.0, 41, 197921.857, 2801548.764),
                                     (12.1017, 35.5192, 36, 774215.726, 1339063.906)):
            E, N = s2.utm_forward(lat, lon, zone)
            self.assertAlmostEqual(float(E), e, places=2)
            self.assertAlmostEqual(float(N), n, places=2)
        self.assertEqual(s2.projection_of({"proj:code": "EPSG:32639"}), {"zone": 39, "south": False})
        self.assertEqual(s2.projection_of({"proj:epsg": 32736}), {"zone": 36, "south": True})

    def test_pixels_land_on_their_own_ground_across_tile_borders(self):
        passes = self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        self.assertEqual([p["date"] for p in passes], ["2026-11-09", "2026-11-02", "2026-10-25", "2026-10-05"])
        clear = passes[3]
        E, N = self.grid.utm(clear["proj"])
        for quality, tolerance in (("full", 6e-5), ("fast", 6e-5)):
            scene = self.archive.load_scene(clear, self.grid, quality)
            for band in s2.REFLECTANCE:
                expected = BASE[band] + 2e-5 * (E - EC) + 1e-5 * (N - NC)
                self.assertLess(float(np.abs(scene[band] - expected).max()), tolerance, f"{band} {quality}")
            self.assertTrue((scene["scl"] == 5).all())
        tiffs = [c for c in self.fake.calls if c[1].endswith(".tif")]
        self.assertTrue(all(c[2] and c[2].startswith("bytes=") for c in tiffs), "pictures are read by range only")
        self.assertEqual(len([c for c in tiffs if c[1].endswith("B04.tif")]), 1 + 4 + 1, "header, four tiles, one overview tile")

    def test_cloud_is_judged_over_the_box_and_the_pair_is_the_nearest_clear_passes(self):
        passes = self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        for p in passes:
            self.archive.assess(p, self.grid)
        self.assertEqual([round(p["sky"]["hidden"], 2) for p in passes], [0.0, 1.0, 1.0, 0.0])
        before, after = s2.pick_pair(passes, T("2026-10-28T10:00:00Z"), T("2026-10-28T12:00:00Z"))
        self.assertEqual((before["date"], after["date"]), ("2026-10-05", "2026-11-09"))
        before, after = s2.pick_pair(passes, T("2026-11-09T12:00:00Z"), T("2026-11-09T12:00:00Z"))
        self.assertEqual((before["date"], after), ("2026-11-09", None))

    def test_changed_area_and_flames(self):
        passes = self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        A, B = self.archive.load_scene(passes[0], self.grid), self.archive.load_scene(passes[3], self.grid)
        change = s2.change_of(B, A, self.grid.res)
        self.assertGreaterEqual(change["dark_ha"], 1.0)                 # the burnt hectare
        self.assertLessEqual(change["area_ha"], 1.6)                    # plus the small patch the flame changed
        self.assertEqual(change["compared"], 1.0)
        self.assertFalse(change["clouds"])
        self.assertEqual((s2.hot_count(A), s2.hot_count(B)), (1, 0))
        self.assertEqual(s2.change_of(B, B, self.grid.res)["area_ha"], 0.0)
        layer = s2.change_layer(change)
        self.assertTrue((layer[change["mask"] & (change["dir"] < 0)][:, :3] == (255, 59, 48)).all())
        self.assertEqual(int(layer[~change["mask"]][:, 3].max()), 0)
        # cloud on one date only is left out, not reported as change
        cloudy = self.archive.load_scene(passes[1], self.grid)
        hidden = s2.change_of(B, cloudy, self.grid.res)
        self.assertEqual((hidden["area_ha"], hidden["compared"]), (0.0, 0.0))

    def test_the_archive_being_down_or_slow_is_a_named_failure_within_the_time_limit(self):
        self.fake.fail = lambda url: "down"
        with self.assertRaises(s2.ArchiveError) as e:
            self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        self.assertEqual(e.exception.code, "source")
        self.assertEqual(len(self.fake.calls), 2, "one retry")
        self.fake.fail = lambda url: "slow"
        with self.assertRaises(s2.ArchiveError) as e:
            self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        self.assertEqual(e.exception.code, "timeout")
        self.fake.fail = None
        passes = self.archive.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        self.fake.fail = lambda url: "norange" if url.endswith(".tif") else None
        with self.assertRaises(s2.ArchiveError) as e:
            self.archive.assess(passes[0], self.grid)
        self.assertEqual(e.exception.code, "source")                    # never a whole file by accident
        late = s2.Archive(self.fake, deadline=time.monotonic() - 1)
        with self.assertRaises(s2.ArchiveError) as e:
            late.list_passes(self.grid, T("2026-08-01T00:00:00Z"), T("2026-11-10T00:00:00Z"))
        self.assertEqual(e.exception.code, "timeout")


class PictureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site", app_url="https://app.test/")
        self.fake = FakeArchive(PASSES)
        self.inc = {"id": "20261028-1000_26.000_50.000", "lat": LAT, "lon": LON, "first": "2026-10-28T10:00:00Z",
                    "last": "2026-10-28T12:00:00Z", "status": "alerted", "place": "land", "best": "polar",
                    "polar": [{}], "headline": "حريق في منشأة نفطية (Test Plant)", "where": "قرب موقع الاختبار", "tg_msg": 77}

    def work(self, now):
        return satpic.work_one(self.cfg, T(now), session=self.fake)

    def test_only_alerted_fires_on_land_get_a_picture_and_only_once(self):
        now = T("2026-10-29T00:00:00Z")
        self.assertTrue(satpic.wanted(self.inc, self.cfg, now))
        self.assertFalse(satpic.wanted(dict(self.inc, status="new"), self.cfg, now))
        self.assertFalse(satpic.wanted(dict(self.inc, place="sea"), self.cfg, now))
        self.assertFalse(satpic.wanted(dict(self.inc, recovered_historical=True), self.cfg, now))
        self.assertFalse(satpic.wanted(dict(self.inc, satpic={"sent_at": "2026-10-30T00:00:00Z"}), self.cfg, now))
        self.assertFalse(satpic.wanted(self.inc, self.cfg, T("2026-11-12T00:00:00Z")), "after 12 days it stops waiting")
        # switched on in a running monitor: fires alerted before the starting point are left alone
        self.cfg["satpic"]["_since"] = "2026-10-28T00:00:00Z"
        self.assertFalse(satpic.wanted(dict(self.inc, alerted_at="2026-10-27T23:00:00Z"), self.cfg, now))
        self.assertTrue(satpic.wanted(dict(self.inc, alerted_at="2026-10-28T12:10:00Z"), self.cfg, now))
        self.cfg["satpic"]["enabled"] = False
        self.assertFalse(satpic.wanted(self.inc, self.cfg, now))

    def test_the_worker_waits_for_a_clear_pass_then_makes_the_picture_and_its_text(self):
        satpic.request(self.cfg, self.inc, T("2026-10-28T12:10:00Z"))
        # 3 November: the only pass after the fire is under cloud
        self.assertEqual(self.work("2026-11-03T00:00:00Z"), self.inc["id"])
        self.assertIsNone(satpic.collect(self.cfg, self.inc, T("2026-11-03T00:00:00Z")))
        calls = len(self.fake.calls)
        self.assertIsNone(self.work("2026-11-03T01:00:00Z"), "not tried again before three hours")
        self.assertEqual(len(self.fake.calls), calls)
        # 10 November: the pass of the 9th is clear
        self.assertEqual(self.work("2026-11-10T00:00:00Z"), self.inc["id"])
        picture, res = satpic.collect(self.cfg, self.inc, T("2026-11-10T00:00:00Z"))
        self.assertEqual((res["before_time"][:10], res["after_time"][:10]), ("2026-10-05", "2026-11-09"))
        with Image.open(picture) as im:
            self.assertEqual((im.size, im.format), ((1080, 1350), "JPEG"))
        self.assertLess(picture.stat().st_size, 900_000)
        area = satpic.area_text(res["area_ha"])
        self.assertEqual(satpic.caption(self.inc, res, self.cfg), "\n".join([
            "🛰 <b>صور الأقمار لموقع الحريق: قبل وبعد</b>",
            "قبل: 5 تشرين الأول · بعد: 9 تشرين الثاني الساعة 10:22 (بغداد)",
            f"تغيّر مرصود: نحو {area} (بالأحمر ما صار أغمق، وبالأزرق ما صار أفتح).",
            "🔥 لهب أو حرارة شديدة ظاهرة لحظة التقاط صورة «بعد».",
            "القمر الأوروبي ⁨Sentinel-2⁩‏، كل نقطة 10 أمتار: يظهر الضرر الكبير فقط، والصورة لا تحدد السبب.",
            '📱 <a href="https://app.test/?e=ba:i:20261028-1000_26.000_50.000#ba:i:20261028-1000_26.000_50.000">افتح المقارنة في التطبيق</a>']))
        self.assertEqual(satpic.facts(self.inc, res), {"before": "2026-10-05", "after": "2026-11-09",
                                                      "area_ha": round(res["area_ha"], 2), "quiet": False})
        self.assertIsNone(self.work("2026-11-10T06:00:00Z"), "a finished picture is not made again")
        # a later sighting of the same fire asks for a later picture: the old result no longer fits
        moved = dict(self.inc, last="2026-11-09T09:00:00Z")
        self.assertIsNone(satpic.collect(self.cfg, moved, T("2026-11-10T00:00:00Z")))

    def test_wording_for_no_change_clouds_and_a_missing_before_picture(self):
        res = {"after_time": "2026-11-09T07:22:30Z", "before_time": "2026-10-05T07:22:30Z", "area_ha": 0.1, "share": 0.0001,
               "compared": 0.7, "clouds": True, "hot_after": 2, "hot_before": 1}
        text = satpic.caption(self.inc, res, dict(self.cfg, app_url=""))
        self.assertIn("لا تغيّر واضح في الموقع بهذه الدقة", text)
        self.assertIn("غيوم في إحدى الصورتين: التقدير أقل ثقة.", text)
        self.assertIn("(وكانت ظاهرة قبل الحريق أيضاً: شعلة غاز على الأرجح).", text)
        self.assertNotIn("مؤكد", text)
        self.assertNotIn("ضرر مؤكد", text)
        alone = satpic.caption(self.inc, dict(res, before_time=None), dict(self.cfg, app_url=""))
        self.assertIn("لا صورة صافية قبل الحريق للمقارنة", alone)
        self.assertEqual(satpic.area_text(0.25), "2,500 م²")
        self.assertEqual(satpic.area_text(12.34), "12.3 هكتار")
        self.assertEqual(satpic.area_text(250), "2.50 كم²")

    def test_no_clear_pass_in_time_closes_the_request_quietly(self):
        self.fake = FakeArchive([{"date": "2026-10-05"}, {"date": "2026-11-02", "cloud": "all"}, {"date": "2026-11-07", "cloud": "all"}])
        satpic.request(self.cfg, self.inc, T("2026-10-28T12:10:00Z"))
        self.work("2026-11-10T00:00:00Z")
        self.assertIsNone(satpic.collect(self.cfg, self.inc, T("2026-11-10T00:00:00Z")))
        self.assertIn("closed", self.inc["satpic"])
        self.assertFalse(satpic.wanted(self.inc, self.cfg, T("2026-11-10T00:00:00Z")))

    def test_an_archive_outage_is_retried_later_not_closed(self):
        satpic.request(self.cfg, self.inc, T("2026-10-28T12:10:00Z"))
        self.fake.fail = lambda url: "down"
        self.work("2026-11-10T00:00:00Z")
        self.assertIsNone(satpic.collect(self.cfg, self.inc, T("2026-11-10T00:00:00Z")))
        self.assertNotIn("satpic", self.inc)
        self.fake.fail = None
        self.assertIsNone(self.work("2026-11-10T01:00:00Z"))
        self.assertEqual(self.work("2026-11-10T03:30:00Z"), self.inc["id"])
        self.assertIsNotNone(satpic.collect(self.cfg, self.inc, T("2026-11-10T03:30:00Z")))


class WatchedPlaceTests(unittest.TestCase):
    """Places watched whether or not anything burns: a new clear pass is compared with the one
    before it, and only a patch of clear change is posted."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site", app_url="https://app.test/")
        self.cfg["watch"]["places"] = [{"name": "مطار الاختبار (Test Airport)", "lat": LAT, "lon": LON, "half_km": 1},
                                       {"name": "", "lat": 1, "lon": 2}, {"name": "بلا إحداثيات"}]
        self.state = {}
        self.fake = FakeArchive(PASSES)

    def look(self, now):
        """One round of the scanner and the worker, as in the live loop."""
        satpic.watch_request(self.cfg, self.state, T(now))
        satpic.watch_work_one(self.cfg, T(now), session=self.fake)
        return satpic.watch_results(self.cfg, self.state, T(now))

    def remember(self, results):
        for place, res, picture in results:
            self.state.setdefault("watch", {})[place["key"]] = {"pass": res["pass"]}

    def test_only_complete_places_are_watched(self):
        places = satpic.watch_places(self.cfg)
        self.assertEqual([(p["name"], p["half_km"]) for p in places], [("مطار الاختبار (Test Airport)", 1.0)])

    def test_the_first_clear_pass_is_the_starting_point_then_a_changed_pass_is_posted_once(self):
        # 3 November: the newest clear pass is 5 October (the two after it are cloudy): the starting point
        results = self.look("2026-11-03T00:00:00Z")
        self.assertEqual([(r["pass"][:10], r["notable"], pic) for _, r, pic in results], [("2026-10-05", False, None)])
        self.remember(results)
        self.assertEqual(self.look("2026-11-03T04:00:00Z"), [], "nothing new: silent")
        calls = len(self.fake.calls)
        satpic.watch_work_one(self.cfg, T("2026-11-03T05:00:00Z"), session=self.fake)
        self.assertEqual(len(self.fake.calls), calls, "not looked at again before three hours have passed")
        # 10 November: the pass of the 9th is clear and a hectare has darkened
        results = self.look("2026-11-10T00:00:00Z")
        self.assertEqual(len(results), 1)
        place, res, picture = results[0]
        self.assertTrue(res["notable"])
        self.assertGreaterEqual(res["patch_ha"], 0.9)
        self.assertEqual((res["before_time"][:10], res["after_time"][:10]), ("2026-10-05", "2026-11-09"))
        with Image.open(picture) as im:
            self.assertEqual(im.size, (1080, 1350))
        text = satpic.watch_caption(place, res, self.cfg)
        self.assertEqual(text.split("\n")[0], "🛰 <b>تغيّر مرصود في موقع مراقَب: مطار الاختبار (\u2068Test Airport\u2069\u200f)</b>")
        self.assertIn("قبل: 5 تشرين الأول · بعد: 9 تشرين الثاني الساعة 10:22 (بغداد)", text)
        self.assertIn(f"أكبر بقعة متغيّرة نحو {satpic.area_text(res['patch_ha'])}", text)
        self.assertIn("🔥 لهب أو حرارة شديدة ظاهرة لحظة التقاط صورة «بعد»، ولم تكن ظاهرة قبل.", text)
        self.assertIn("قد يكون التغيّر طبيعياً", text)
        self.assertIn("<code>26.0000, 50.0000</code>", text)
        self.assertIn('href="https://app.test/?e=ba%3A26.00000%2C50.00000%2C1%2C20261005%2C20261109%3B', text)
        self.assertNotIn("مؤكد", text)
        self.remember(results)
        self.assertEqual(self.look("2026-11-10T04:00:00Z"), [], "handled: not offered again")

    def test_a_new_pass_without_a_clear_patch_is_remembered_silently(self):
        self.fake = FakeArchive([{"date": "2026-10-05"}, {"date": "2026-11-04"}, {"date": "2026-11-09"}])
        self.remember(self.look("2026-11-06T00:00:00Z"))                   # starting point: 4 November
        results = self.look("2026-11-10T00:00:00Z")
        self.assertEqual([(r["pass"][:10], r["notable"], pic) for _, r, pic in results], [("2026-11-09", False, None)])
        self.assertLess(results[0][1]["patch_ha"], 0.5)

    def test_a_place_taken_off_the_list_is_no_longer_looked_at(self):
        self.look("2026-11-03T00:00:00Z")
        self.cfg["watch"]["places"] = []
        self.assertEqual(self.look("2026-11-10T00:00:00Z"), [])
        self.assertEqual(list((satpic.queue_dir(self.cfg) / "watch").glob("*.job.json")), [])


class AlertTextTests(unittest.TestCase):
    def test_a_fire_nasa_alone_saw_is_not_promised_a_video(self):
        nasa = {"first": "2026-11-10T09:43:00Z", "geo_slots": [], "polar": [{"conf": "n"}]}
        self.assertIn("لا يُتوقع فيديو", video_note(nasa))
        geo = {"first": "2026-11-10T12:10:00Z", "geo_slots": ["x"], "polar": []}
        self.assertEqual(video_note(geo), "🎞 الفيديو قيد التجهيز؛ سيُرسل عند توفر صور واضحة للرصد.")


class CycleTests(unittest.TestCase):
    """A whole check: the picture is asked for, posted once as a reply, and remembered."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = T("2026-11-10T00:00:00Z")
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo", app_url="https://app.test/")
        self.cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [47.8, 23.5, 56.6, 30.5]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["clip"]["enabled"] = False
        save_json(self.cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                          "sea_check": engine.SEA_CHECK})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        self.incident = {
            "id": "20261031-1000_26.000_50.000", "kind": "new", "first": "2026-10-31T10:00:00Z", "last": "2026-10-31T12:00:00Z",
            "lat": LAT, "lon": LON, "best": "polar", "families": ["viirs", "mtg"], "srcs": ["VIIRS_NOAA21_NRT", "mtg_frp"],
            "geo_slots": [], "polar": [], "max_frp": 80.0, "frp_series": [["2026-10-31T10:00:00Z", 80.0, "viirs"]],
            "n": 4, "status": "alerted", "verdict": "مؤكد", "alerted_verdict": "مؤكد", "alerted_max_frp": 80.0,
            "alerted_at": "2026-10-31T12:10:00Z", "alert_updated_at": "2026-10-31T12:10:00Z", "tg_msg": 77,
            "known_days": 0, "area_days": 0, "place": "land", "country": "السعودية", "facility": None,
            "headline": "حريق في منشأة نفطية", "where": "قرب موقع الاختبار",
            "priority": "high", "_pos": [26.0, 50.0, "offline-map"]}
        save_json(self.cfg["_state_dir"] / "incidents.json", [self.incident])
        session = Mock()
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No network in a check")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "land"
        geo.facility.return_value = None
        geo.describe.return_value = ("قرب موقع الاختبار", "السعودية")
        self.deliver = Mock(return_value=901)
        patches = patch.multiple(
            engine, utcnow=Mock(side_effect=lambda: self.now), make_session=Mock(return_value=session),
            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
            latest_time=Mock(side_effect=AssertionError("No source requests")), deliver=self.deliver)
        patches.start()
        self.addCleanup(patches.stop)

    def stored(self, **changes):
        self.incident.update(changes)
        save_json(self.cfg["_state_dir"] / "incidents.json", [self.incident])

    def running_since(self, when):
        save_json(self.cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                          "sea_check": engine.SEA_CHECK, "satpic_since": when})

    def test_the_picture_is_posted_once_as_a_reply_and_remembered(self):
        self.running_since("2026-10-30T00:00:00Z")
        self.stored(first="2026-11-06T10:00:00Z", last="2026-11-06T12:00:00Z", frp_series=[["2026-11-06T10:00:00Z", 80.0, "viirs"]],
                    alerted_at="2026-11-06T12:10:00Z", alert_updated_at="2026-11-06T12:10:00Z")
        engine.run_cycle(self.cfg, send=False)                      # asks for the picture; nothing to post yet
        self.deliver.assert_not_called()
        self.assertEqual(satpic.work_one(self.cfg, self.now, session=FakeArchive(PASSES)), self.incident["id"])
        state = engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_called_once()
        args, kwargs = self.deliver.call_args
        self.assertTrue(args[2].startswith("🛰 <b>صور الأقمار لموقع الحريق: قبل وبعد</b>"))
        self.assertEqual(Path(args[3]).suffix, ".jpg")
        self.assertTrue(Path(args[3]).is_file())
        self.assertEqual(kwargs["reply_to"], 77)
        kept = state.incidents[0]["satpic"]
        self.assertEqual((kept["before"], kept["after"], kept["sent_at"]), ("2026-10-05", "2026-11-09", iso(self.now)))
        shown = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})
        self.assertEqual([i["satpic"]["after"] for i in shown["items"]], ["2026-11-09"], "the app knows the two dates")
        self.now = T("2026-11-10T00:05:00Z")                        # the next check: never twice
        state = engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_called_once()
        self.assertEqual(state.incidents[0]["satpic"]["sent_at"], "2026-11-10T00:00:00Z")

    def test_switching_the_feature_on_starts_three_days_back_and_two_ready_pictures_go_out_one_per_check(self):
        second = dict(self.incident, id="20261107-0900_26.000_50.000", first="2026-11-07T09:00:00Z", last="2026-11-07T09:30:00Z",
                      alerted_at="2026-11-07T12:00:00Z", tg_msg=88, _pos=[26.0, 50.0, "offline-map"])
        first = dict(self.incident, first="2026-11-08T05:00:00Z", last="2026-11-08T06:00:00Z", alerted_at="2026-11-08T08:00:00Z")
        old = dict(self.incident, id="20261105-0900_26.000_50.000", first="2026-11-05T09:00:00Z", last="2026-11-05T09:30:00Z",
                   alerted_at="2026-11-05T12:00:00Z", tg_msg=66)
        save_json(self.cfg["_state_dir"] / "incidents.json", [first, second, old])
        state = engine.run_cycle(self.cfg, send=False)
        self.assertEqual(state.state["satpic_since"], "2026-11-07T00:00:00Z")
        jobs = sorted(p.name for p in (satpic.queue_dir(self.cfg) / "jobs").glob("*.json"))
        self.assertEqual(len(jobs), 2, "the fire alerted five days ago gets no picture")
        archive = FakeArchive(PASSES)
        self.assertIsNotNone(satpic.work_one(self.cfg, self.now, session=archive))
        self.assertIsNotNone(satpic.work_one(self.cfg, self.now, session=archive))
        engine.run_cycle(self.cfg, send=False)
        self.assertEqual(self.deliver.call_count, 1)
        self.now = T("2026-11-10T00:03:00Z")
        engine.run_cycle(self.cfg, send=False)
        self.assertEqual(self.deliver.call_count, 2)
        self.assertEqual({c.kwargs["reply_to"] for c in self.deliver.call_args_list}, {77, 88})
        self.now = T("2026-11-10T00:06:00Z")
        engine.run_cycle(self.cfg, send=False)
        self.assertEqual(self.deliver.call_count, 2)

    def test_a_watched_place_is_posted_as_its_own_post_once_and_remembered_in_the_state(self):
        self.stored(status="new", first="2026-11-09T20:00:00Z", last="2026-11-09T20:00:00Z")      # an unrelated small fire
        self.cfg["watch"]["places"] = [{"name": "ميناء الاختبار", "lat": LAT, "lon": LON, "half_km": 1}]
        archive = FakeArchive(PASSES)
        self.now = T("2026-11-03T00:00:00Z")
        engine.run_cycle(self.cfg, send=False)                                # writes the job
        satpic.watch_work_one(self.cfg, self.now, session=archive)            # the starting point (5 October)
        state = engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_not_called()
        key = satpic.watch_places(self.cfg)[0]["key"]
        self.assertEqual(state.state["watch"][key]["pass"][:10], "2026-10-05")
        self.now = T("2026-11-10T00:00:00Z")
        engine.run_cycle(self.cfg, send=False)                                # tells the worker what is known
        satpic.watch_work_one(self.cfg, self.now, session=archive)
        state = engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_called_once()
        args, kwargs = self.deliver.call_args
        self.assertTrue(args[2].startswith("🛰 <b>تغيّر مرصود في موقع مراقَب: ميناء الاختبار</b>"))
        self.assertEqual(Path(args[3]).suffix, ".jpg")
        self.assertIsNone(kwargs.get("reply_to"))
        self.assertEqual((state.state["watch"][key]["pass"][:10], state.state["watch"][key]["sent_at"]), ("2026-11-09", "2026-11-10T00:00:00Z"))
        self.now = T("2026-11-10T04:00:00Z")
        engine.run_cycle(self.cfg, send=False)
        satpic.watch_work_one(self.cfg, self.now, session=archive)
        engine.run_cycle(self.cfg, send=False)
        self.deliver.assert_called_once()

    def test_an_alerted_fire_older_than_a_week_is_kept_while_it_waits_for_its_picture(self):
        self.running_since("2026-10-30T00:00:00Z")
        state = engine.run_cycle(self.cfg, send=False)              # ten days old: normally forgotten after seven
        self.deliver.assert_not_called()
        self.assertEqual(len(state.incidents), 1)
        self.stored(status="new")                                   # a fire that was never posted is not kept for it
        self.assertEqual(engine.run_cycle(self.cfg, send=False).incidents, [])

if __name__ == "__main__":
    unittest.main()
