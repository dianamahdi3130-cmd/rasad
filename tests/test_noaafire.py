"""NASA's NOAA-20/21 pixels read from NOAA's own public files, about two hours before FIRMS has them.

Measured on 14-15 Nov 2026 over Rasad's regions: 99% of NOAA's pixels are FIRMS rows (median 60 m
apart), confidence identical in 400 of 400 sightings, files out 16-30 min after a day pass and 39-49
min after a night pass (docs/reviews/noaa_files_20261116.md). The same pixel must never count twice."""
import copy
import datetime as dt
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import h5py
import numpy as np

from rasad import engine, noaafire
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json

KEY = "VIIRS_EFIRE_VIIRSI_EDR/2026/11/15/EFIRE-VIIRSI_v1r3_n20_s202611152214035_e202611152215280_c202611152301000.nc"
BOX = [32.0, 12.0, 60.0, 37.4]


def efire(points, lines=1536):
    """A small EFIRE file: points are (lat, lon, frp, mask, day, line, area)."""
    out = io.BytesIO()
    with h5py.File(out, "w") as f:
        f.attrs["geospatial_bounds"] = np.bytes_(b"POLYGON((50.0 20.0, 60.0 20.0, 60.0 26.0, 50.0 26.0, 50.0 20.0))")
        f.create_dataset("y", data=np.zeros(lines, ">f4"))
        cols = list(zip(*points)) if points else [[]] * 7
        for name, values, kind in (("FP_Latitude", cols[0], "f4"), ("FP_Longitude", cols[1], "f4"),
                                   ("FP_FireRadiativePower", cols[2], "f4"), ("FP_FireMask", cols[3], "i2"),
                                   ("FP_Day", cols[4], "i2"), ("FP_Line", cols[5], "i2"),
                                   ("FP_PixelArea", cols[6], "f4")):
            f.create_dataset(name, data=np.asarray(values, kind))
    return out.getvalue()


# 15 Nov 2026, NOAA-20 night pass over the Gulf: a fire in the Emirates and one in the Arabian Sea
NIGHT = [(24.4321, 54.4567, 12.5, 8, 0, 100, 0.554),        # nominal, at nadir
         (17.0123, 57.9876, 3.1, 9, 0, 1400, 2.216),         # high, at the edge of the swath (wider pixel)
         (40.0, 20.0, 50.0, 8, 0, 700, 0.6)]                 # outside Rasad's box: left out


class Reading(unittest.TestCase):
    def test_a_file_becomes_rows_like_firms(self):
        rows = noaafire.rows_from(efire(NIGHT), KEY, "n20", BOX)
        self.assertEqual(len(rows), 2)
        first, edge = rows
        self.assertEqual({k: first[k] for k in ("src", "family", "kind", "satellite", "instrument", "conf", "night")},
                         {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "satellite": "N20",
                          "instrument": "VIIRS", "conf": "n", "night": True})
        self.assertEqual((first["lat"], first["lon"], first["frp"]), (24.4321, 54.4567, 12.5))
        self.assertEqual(first["t"], "2026-11-15T22:14:00Z", "scan line 100 of 1536: 5 s into the granule")
        self.assertEqual(edge["t"], "2026-11-15T22:15:00Z", "scan line 1400: 77 s into the granule")
        self.assertEqual(edge["conf"], "h")
        self.assertEqual((first["scan"], first["track"]), (0.37, 0.37), "the nadir pixel as FIRMS gives it (sqrt of scan x track)")
        self.assertAlmostEqual(edge["scan"], 0.74, delta=0.01)
        self.assertEqual(first["via"], "noaa")

    def test_the_outline_of_a_file_from_its_first_bytes(self):
        head = efire(NIGHT)[:32768]
        poly = noaafire.outline(head)
        self.assertEqual(poly[0], (50.0, 20.0))
        self.assertTrue(noaafire.bounds_touch(poly, BOX))
        self.assertFalse(noaafire.bounds_touch([(100.0, 0.0), (110.0, 5.0)], BOX))
        self.assertFalse(noaafire.bounds_touch([(170.0, 0.0), (-170.0, 5.0)], BOX), "a granule across the date line")


class FakeBucket:
    """S3 as NOAA serves it: a listing (with start-after), byte ranges, whole files."""

    def __init__(self, files):
        self.files = files                     # key -> bytes
        self.calls = []
        self.fail = set()

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {}), timeout))
        r = Mock(status_code=200)
        r.raise_for_status = Mock()
        if params:                             # a listing
            after = params.get("start-after") or ""
            keys = sorted(k for k in self.files if k.startswith(params["prefix"]) and k > after and "n20" in url and "_n20_" in k
                          or k.startswith(params["prefix"]) and k > after and "n21" in url and "_n21_" in k)
            body = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
            r.content = (f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                         f"{body}</ListBucketResult>").encode()
            return r
        key = url.split(".amazonaws.com/", 1)[1]
        if key in self.fail:
            raise noaafire.requests.ReadTimeout()
        data = self.files[key]
        r.content = data[:32768] if headers and "Range" in headers else data
        return r


class Collecting(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 15, 22, 50, tzinfo=UTC)

    def setUp(self):
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["noaa"]["enabled"] = True
        far = efire([(5.0, 100.0, 9.0, 8, 0, 10, 0.6)]).replace(
            b"POLYGON((50.0 20.0, 60.0 20.0, 60.0 26.0, 50.0 26.0, 50.0 20.0))",
            b"POLYGON((99.0 1.00, 110.0 1.00, 110.0 9.00, 99.00 9.00, 99.0 1.00))")
        self.bucket = FakeBucket({
            KEY: efire(NIGHT),
            KEY.replace("s202611152214035_e202611152215280", "s202611152215290_e202611152216535"): far,
        })

    def test_new_files_are_read_once_and_only_those_over_the_gulf_are_downloaded(self):
        state, status = {}, {}
        rows = noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX, status=status)
        self.assertEqual(len(rows), 2)
        self.assertEqual((status["read"], status["over"], status["pixels"], status["ok"]), (2, 1, 2, True))
        downloads = [c for c in self.bucket.calls if not c[1] and "Range" not in c[2]]
        self.assertEqual(len(downloads), 1, "the file over Asia was only peeked at")
        self.assertTrue(all(c[3] is not None and c[3] <= 10 for c in self.bucket.calls), "every request has a limit")
        self.bucket.calls.clear()
        self.assertEqual(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX), [], "nothing new the next time")
        self.assertFalse([c for c in self.bucket.calls if not c[1]])

    def test_a_file_that_fails_is_read_again_next_time(self):
        self.bucket.fail = {KEY}
        state = {}
        self.assertEqual(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX), [])
        self.assertNotIn(KEY, state["noaa"]["n20"], "not handled: asked again")
        self.bucket.fail = set()
        self.assertEqual(len(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX)), 2)

    def test_a_file_published_after_a_later_one_is_still_read(self):
        # 15 Nov, NOAA-21: the granule of 22:59 (196 pixels over the Gulf) came out after those of 23:00-23:05
        later = KEY.replace("s202611152214035_e202611152215280", "s202611152230000_e202611152231240")
        files = self.bucket.files
        self.bucket.files = {later: files[KEY]}
        state = {}
        self.assertEqual(len(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX)), 2)
        self.bucket.files = {**files, later: files[KEY]}        # the earlier granule appears one check later
        rows = noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX)
        self.assertEqual([r["t"] for r in rows], ["2026-11-15T22:14:00Z", "2026-11-15T22:15:00Z"])

    def test_an_odd_file_is_left_and_the_rest_is_read(self):
        state, status = {}, {}
        with patch.object(noaafire, "rows_from", side_effect=IndexError("arrays of different lengths")):
            self.assertEqual(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX, status=status), [])
        self.assertIn(KEY, state["noaa"]["n20"], "handled: not downloaded again and again")
        self.assertEqual(status["failed"], 1)

    def test_freshness_is_the_newest_file_handled(self):
        status = {}
        noaafire.collect(self.cfg, self.bucket, {}, self.NOW, BOX, status=status)
        self.assertEqual(status["latest"], "2026-11-15T22:16:53Z", "the end of the newest file read, not of the last fire")

    def test_old_files_are_not_read_as_news(self):
        state = {}
        later = self.NOW + dt.timedelta(hours=7)          # files older than six hours are left to FIRMS
        self.assertEqual(noaafire.collect(self.cfg, self.bucket, state, later, BOX), [])


class TwinsOnce(unittest.TestCase):
    def twins(self, noaa_points, src="VIIRS_NOAA20_NRT", t="2026-11-15T23:54:00Z"):
        noaa = {}
        twins = noaafire.Twins({}, noaa)
        for lat, lon in noaa_points:
            k = f"{src}|{t}|{lat}|{lon}"
            noaa[k] = 0
            twins.add(k, {"src": src, "t": t, "lat": lat, "lon": lon}, noaa=True)
        return twins, noaa

    def test_the_firms_copy_of_a_pixel_is_absorbed_once(self):
        twins, noaa = self.twins([(24.4321, 54.4567)], t="2026-11-15T22:14:00Z")
        firms_a = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T22:15:00Z", "lat": 24.4325, "lon": 54.4571}
        neighbour = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T22:14:00Z", "lat": 24.4355, "lon": 54.4567}
        other_sat = dict(firms_a, src="VIIRS_NOAA21_NRT")
        modis = dict(firms_a, src="MODIS_NRT")
        self.assertEqual(twins.match([firms_a, neighbour, other_sat, modis]), {0: "VIIRS_NOAA20_NRT|2026-11-15T22:14:00Z|24.4321|54.4567"})
        self.assertEqual(sum(noaa.values()), 1)
        self.assertEqual(twins.match([dict(firms_a)]), {}, "a NOAA pixel has one twin only")

    def test_pairs_near_the_edge_of_the_swath_are_not_crossed(self):
        # NOAA-20, 15 Nov 23:54 UTC, Iraq: NOAA's pixels sit about 0.25 km east of FIRMS's. Taking the
        # nearest for each FIRMS row in turn paired A' with B, and B' (0.74 km from A) counted again.
        twins, noaa = self.twins([(35.384, 44.2199), (35.3855, 44.2147)])
        a2 = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T23:54:00Z", "lat": 35.3844, "lon": 44.2171}
        b2 = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T23:54:00Z", "lat": 35.3858, "lon": 44.212}
        self.assertEqual(sorted(twins.match([a2, b2])), [0, 1], "both FIRMS rows are copies")
        self.assertEqual(sum(noaa.values()), 2)

    def test_a_noaa_file_late_after_firms_does_not_count_again(self):
        k = "VIIRS_NOAA21_NRT|2026-11-15T10:23:00Z|26.1|55.9"
        twins = noaafire.Twins({k: "2026-11-15T10:23:00Z"}, {})
        late = {"src": "VIIRS_NOAA21_NRT", "t": "2026-11-15T10:24:00Z", "lat": 26.1003, "lon": 55.9002, "via": "noaa"}
        self.assertTrue(twins.absorbs(late))


class FaintSeaAndLinks(unittest.TestCase):
    """A faint night pixel at sea seen only in NOAA's file is not yet a second look; the «ناسا» link
    says the map shows it later."""

    def test_faint_noaa_only_pixel_waits_for_firms(self):
        from rasad import brain
        base = {"place": "sea", "kind": "new", "known_days": 0, "facility": None}
        p1 = {"t": "2026-11-14T22:34:00Z", "frp": 0.46, "conf": "n", "night": True, "src": "VIIRS_NOAA20_NRT", "family": "viirs", "via": "noaa"}
        p2 = {"t": "2026-11-14T23:17:00Z", "frp": 0.90, "conf": "n", "night": True, "src": "VIIRS_NOAA21_NRT", "family": "viirs", "via": "noaa"}
        self.assertFalse(brain.open_sea_fire(dict(base, polar=[p1, p2])), "14 Nov, 29.33 N 49.49 E: two faint NOAA-only pixels")
        confirmed = {k: v for k, v in p2.items() if k != "via"}           # FIRMS has brought its copy
        self.assertTrue(brain.open_sea_fire(dict(base, polar=[dict(p1, via=None), confirmed])))
        strong = dict(p1, frp=3.0)
        self.assertTrue(brain.open_sea_fire(dict(base, polar=[strong])), "a pixel of some strength counts at once")

    def test_the_nasa_link_says_its_map_shows_it_later(self):
        from rasad import brain
        self.assertIn("ناسا (تظهر بعد نحو 3 ساعات)", brain.links(26.0, 56.0, True, nasa_later=True))
        self.assertIn("ناسا", brain.links(26.0, 56.0, True))


class WholeCheck(unittest.TestCase):
    """The pixel from NOAA's file makes the incident; FIRMS's copy three hours later adds nothing."""
    NOW = dt.datetime(2026, 11, 15, 22, 50, tzinfo=UTC)

    def check(self, root, noaa_rows, firms_rows, now):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [50, 22, 57, 27]}]
        cfg["seawatch"].update(enabled=False, look_back=False)
        cfg["eumetview"]["fire_layers"] = []
        cfg["clip"]["enabled"] = False
        cfg["firms"]["map_key"] = "offline-test-key"
        cfg["noaa"]["enabled"] = True
        session = Mock()
        session.get.side_effect = AssertionError("No network")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "land"
        geo.facility.return_value = {"label": "مصفاة", "code": "refinery", "km": 0.5}
        geo.describe.return_value = ("الإمارات", "الإمارات")
        forbidden = Mock(side_effect=AssertionError("Unexpected source"))
        with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(return_value=session),
                            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
                            firms_fetch=Mock(return_value=copy.deepcopy(firms_rows)),
                            latest_time=forbidden, detect_geo_layer=forbidden,
                            deliver=Mock(return_value=None), tg_send=forbidden), \
                patch.object(engine.noaafire, "collect", Mock(return_value=copy.deepcopy(noaa_rows))):
            return engine.run_cycle(cfg, send=False)

    @staticmethod
    def memory(root):
        memory = Memory({})
        memory.record(30, 48, "2026-11-14", 10)
        save_json(root / "state" / "memory.json", memory.cells)

    def _check_raising(self, root, firms_rows):
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [50, 22, 57, 27]}]
        cfg["seawatch"].update(enabled=False, look_back=False)
        cfg["eumetview"]["fire_layers"] = []
        cfg["clip"]["enabled"] = False
        cfg["firms"]["map_key"] = "offline-test-key"
        cfg["noaa"]["enabled"] = True
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "land"
        geo.facility.return_value = {"label": "مصفاة", "code": "refinery", "km": 0.5}
        geo.describe.return_value = ("الإمارات", "الإمارات")
        forbidden = Mock(side_effect=AssertionError("Unexpected source"))
        with patch.multiple(engine, utcnow=Mock(return_value=self.NOW), make_session=Mock(), WMS=Mock(),
                            Geo=Mock(return_value=geo), Availability=Mock(),
                            firms_fetch=Mock(return_value=copy.deepcopy(firms_rows)),
                            latest_time=forbidden, detect_geo_layer=forbidden,
                            deliver=Mock(return_value=None), tg_send=forbidden), \
                patch.object(engine.noaafire, "collect", Mock(side_effect=RuntimeError("damaged chunk"))):
            return engine.run_cycle(cfg, send=False)

    def test_one_sighting_whichever_road_it_came_by(self):
        rows = noaafire.rows_from(efire(NIGHT[:1]), KEY, "n20", BOX)
        firms = [{"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-15T22:14:00Z",
                  "lat": 24.4324, "lon": 54.4569, "frp": 12.3, "conf": "nominal", "night": True,
                  "scan": 0.48, "track": 0.48, "instrument": "VIIRS", "satellite": "N20"}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / "state" / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                     "sea_check": engine.SEA_CHECK, "bootstrapped": iso(self.NOW),
                                                     "hello_sent": iso(self.NOW)})
            memory = Memory({})
            memory.record(30, 48, "2026-11-14", 10)
            save_json(root / "state" / "memory.json", memory.cells)
            first = self.check(root, rows, [], self.NOW)                       # 22:50: NOAA's file only
            self.assertEqual(len(first.incidents), 1)
            inc = first.incidents[0]
            self.assertEqual((inc["families"], len(inc["polar"]), inc["max_frp"]), (["viirs"], 1, 12.5))
            later = self.check(root, [], firms, self.NOW + dt.timedelta(hours=3))   # 01:50: FIRMS brings it again
            again = later.incidents[0]
            self.assertEqual((len(again["polar"]), again["n"]), (1, inc["n"]), "the FIRMS copy is the same sighting")
            self.assertEqual(again["families"], ["viirs"], "never a second family")
            self.assertNotIn("via", again["polar"][0], "the reading now shows on NASA's map too")
            state = load_json(root / "state" / "state.json", {})
            self.assertEqual(state["noaa_seen"], {}, "paired: the NOAA pixel is not needed any more")

    def test_until_firms_has_it_the_reading_and_the_post_say_so(self):
        rows = noaafire.rows_from(efire(NIGHT[:1]), KEY, "n20", BOX)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / "state" / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                     "sea_check": engine.SEA_CHECK, "bootstrapped": iso(self.NOW),
                                                     "hello_sent": iso(self.NOW)})
            self.memory(root)
            inc = self.check(root, rows, [], self.NOW).incidents[0]
            self.assertEqual(inc["polar"][0]["via"], "noaa")
            from rasad.brain import alert_message
            cfg = copy.deepcopy(engine.DEFAULTS)
            self.assertIn("ناسا (تظهر بعد نحو 3 ساعات)", alert_message(inc, cfg))
            later = self.check(root, [], [], self.NOW + dt.timedelta(days=2, hours=1))
            self.assertEqual(load_json(root / "state" / "state.json", {})["noaa_seen"], {}, "unpaired: forgotten after two days")

    def test_an_error_in_the_noaa_source_never_stops_the_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / "state" / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                     "sea_check": engine.SEA_CHECK, "bootstrapped": iso(self.NOW),
                                                     "hello_sent": iso(self.NOW)})
            self.memory(root)
            firms = [{"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-15T22:14:00Z",
                      "lat": 24.4324, "lon": 54.4569, "frp": 12.3, "conf": "nominal", "night": True,
                      "scan": 0.37, "track": 0.37, "instrument": "VIIRS", "satellite": "N20"}]
            state = self._check_raising(root, firms)
            self.assertEqual(len(state.incidents), 1, "FIRMS was still read and judged")
            self.assertFalse(load_json(root / "site" / "data" / "meta.json", {})["sources"]["noaa"]["ok"])


if __name__ == "__main__":
    unittest.main()
