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
        self.assertEqual((first["scan"], first["track"]), (0.48, 0.48), "the nadir pixel as FIRMS gives it")
        self.assertAlmostEqual(edge["scan"], 0.96, delta=0.01)

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
        self.assertNotIn("n20", state.get("noaa", {}), "the cursor did not move past the failed file")
        self.bucket.fail = set()
        self.assertEqual(len(noaafire.collect(self.cfg, self.bucket, state, self.NOW, BOX)), 2)

    def test_old_files_are_not_read_as_news(self):
        state = {}
        later = self.NOW + dt.timedelta(hours=7)          # files older than six hours are left to FIRMS
        self.assertEqual(noaafire.collect(self.cfg, self.bucket, state, later, BOX), [])


class TwinsOnce(unittest.TestCase):
    def test_the_firms_copy_of_a_pixel_is_absorbed_once(self):
        seen, noaa = {}, {}
        twins = noaafire.Twins(seen, noaa)
        a = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T22:14:00Z", "lat": 24.4321, "lon": 54.4567, "via": "noaa"}
        ka = "VIIRS_NOAA20_NRT|2026-11-15T22:14:00Z|24.4321|54.4567"
        self.assertFalse(twins.absorbs(a))
        noaa[ka] = 0
        twins.add(ka, a, noaa=True)
        firms_a = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T22:15:00Z", "lat": 24.4325, "lon": 54.4571}
        self.assertTrue(twins.absorbs(firms_a), "60 m and a minute apart: the same pixel")
        neighbour = {"src": "VIIRS_NOAA20_NRT", "t": "2026-11-15T22:14:00Z", "lat": 24.4355, "lon": 54.4567}
        self.assertFalse(twins.absorbs(neighbour), "a second pixel 375 m away is not the same one again")
        other_sat = dict(firms_a, src="VIIRS_NOAA21_NRT")
        self.assertFalse(twins.absorbs(other_sat))
        modis = dict(firms_a, src="MODIS_NRT")
        self.assertFalse(twins.absorbs(modis))

    def test_a_noaa_file_late_after_firms_does_not_count_again(self):
        k = "VIIRS_NOAA21_NRT|2026-11-15T10:23:00Z|26.1|55.9"
        twins = noaafire.Twins({k: "2026-11-15T10:23:00Z"}, {})
        late = {"src": "VIIRS_NOAA21_NRT", "t": "2026-11-15T10:24:00Z", "lat": 26.1003, "lon": 55.9002, "via": "noaa"}
        self.assertTrue(twins.absorbs(late))


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
            state = load_json(root / "state" / "state.json", {})
            self.assertEqual(sum(state["noaa_seen"].values()), 1, "the NOAA pixel knows its twin has come")
            self.assertFalse([k for k in state["seen"] if k in state["noaa_seen"]], "NOAA pixels are kept apart, for a day")
            self.check(root, [], [], self.NOW + dt.timedelta(days=1, hours=1))
            self.assertEqual(load_json(root / "state" / "state.json", {})["noaa_seen"], {}, "and forgotten after a day")


if __name__ == "__main__":
    unittest.main()
