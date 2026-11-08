"""Nearby NASA pixels retain their individual positions and measured power."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory
from rasad.util import UTC, iso, load_json, save_json


class DetectionIdentityTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 8, 21, 44, tzinfo=UTC)

    def cycle(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
            cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 25, 57, 27]}]
            cfg["eumetview"]["fire_layers"] = []
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["clip"]["enabled"] = False
            cfg["firms"]["map_key"] = "offline-key"
            save_json(cfg["_state_dir"] / "state.json", {
                "schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                "sea_check": engine.SEA_CHECK, "bootstrapped": iso(self.NOW), "hello_sent": iso(self.NOW)})
            memory = Memory({})
            memory.record(30, 48, "2026-11-07", 10)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            session = Mock()
            for method in ("get", "post", "put"):
                getattr(session, method).side_effect = AssertionError("No network in identity regressions")
            geo = Mock(version="offline-map")
            geo.place_kind.return_value = "sea"
            geo.facility.return_value = None
            geo.describe.return_value = ("في البحر", "الإمارات")
            with patch.multiple(engine, utcnow=Mock(return_value=self.NOW),
                                make_session=Mock(return_value=session), WMS=Mock(),
                                Geo=Mock(return_value=geo), Availability=Mock(),
                                firms_fetch=Mock(return_value=copy.deepcopy(rows)),
                                deliver=Mock(return_value=None), tg_send=Mock()):
                state = engine.run_cycle(cfg, send=False)
            self.assertEqual(len(state.incidents), 1)
            published = load_json(cfg["_site_dir"] / "data" / "incidents.json", {})["items"]
            return state.incidents[0], published[0]

    @staticmethod
    def detection(src, family, t, lat, lon, frp, scan, track, confidence):
        return {"src": src, "family": family, "kind": "polar", "t": t,
                "lat": lat, "lon": lon, "frp": frp, "scan": scan, "track": track,
                "conf": confidence, "night": family == "modis"}

    def test_distinct_viirs_pixels_in_same_rounded_bucket_keep_actual_61_mw_peak(self):
        # Public NOAA-20 acquisition, 8 Nov 2026 10:21 UTC. All positions round
        # to (26.10, 55.90), but these are separate pixels with different FRP.
        rows = [self.detection("VIIRS_NOAA20_NRT", "viirs", "2026-11-08T10:21:00Z", *point)
                for point in ((26.09948, 55.89817, 26.37, .54, .67, "nominal"),
                              (26.10187, 55.90488, 35.24, .54, .67, "nominal"),
                              (26.10148, 55.89945, 61.07, .53, .67, "nominal"))]
        incident, published = self.cycle(rows)
        self.assertEqual(len(incident["polar"]), 3)
        self.assertEqual(incident["max_frp"], 61.07)
        self.assertEqual(published["max_frp"], 61.07)
        self.assertEqual({p["frp"] for p in incident["polar"]}, {26.37, 35.24, 61.07})

    def test_distinct_modis_pixels_in_same_rounded_bucket_keep_actual_50_mw_peak(self):
        # Public Terra acquisition at 17:02 UTC; both positions formerly shared
        # the (26.09, 55.93) de-duplication key.
        rows = [self.detection("MODIS_NRT", "modis", "2026-11-08T17:02:00Z", *point)
                for point in ((26.08609, 55.93579, 31.71, 2.15, 1.43, "89"),
                              (26.08757, 55.93005, 50.59, 2.15, 1.43, "100"))]
        incident, published = self.cycle(rows)
        self.assertEqual(len(incident["polar"]), 2)
        self.assertEqual(incident["max_frp"], 50.59)
        self.assertEqual(published["max_frp"], 50.59)


if __name__ == "__main__":
    unittest.main()
