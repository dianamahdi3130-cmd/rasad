"""Offline regressions for European fire-strength checks at known facilities."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine
from rasad.brain import Memory
from rasad.util import iso, save_json


class FacilitySurgeTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 7, 12, tzinfo=dt.timezone.utc)
    LAT, LON = 30.0, 48.0

    def cycle(self, measured_frp, at_facility=True):
        with tempfile.TemporaryDirectory() as folder:
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg["_state_dir"] = Path(folder) / "state"
            cfg["_site_dir"] = Path(folder) / "site"
            cfg["_geodata_dir"] = Path(folder) / "geo"
            cfg["regions"] = [{"id": "test", "name": "العراق", "bbox": [47, 29, 49, 31]}]
            cfg["seawatch"]["enabled"] = False
            cfg["clip"]["enabled"] = False
            cfg["eumetview"]["fire_layers"] = cfg["eumetview"]["fire_layers"][:1]
            cfg["eumetview"]["lookback_slots"] = 1
            cfg["eumetview"]["catchup_hours"] = 0
            memory = Memory({})
            for ago in (1, 2, 3):
                day = (self.NOW - dt.timedelta(days=ago)).date().isoformat()
                memory.record(self.LAT, self.LON, day, 10.0)
            save_json(cfg["_state_dir"] / "memory.json", memory.cells)
            save_json(cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES})

            observed = self.NOW - dt.timedelta(minutes=30)
            detection = {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": iso(observed),
                         "lat": self.LAT, "lon": self.LON, "frp": None,
                         "_fi": ["mtg_fd:frp", [47, 29, 49, 31], 10, 10, 5, 5, iso(observed)]}
            wms = Mock()
            wms.feature_info.return_value = {} if measured_frp is None else {"frp": measured_frp}
            geo = Mock(version="test")
            geo.facility_within.return_value = at_facility
            geo.facility.return_value = {"label": "مصفاة", "code": "refinery", "km": 0.1}
            geo.place_kind.return_value = "land"
            geo.describe.return_value = ("العراق", "العراق")
            with patch.multiple(engine, utcnow=Mock(return_value=self.NOW), make_session=Mock(),
                                WMS=Mock(return_value=wms), Geo=Mock(return_value=geo),
                                Availability=Mock(), latest_time=Mock(return_value=observed),
                                detect_geo_layer=Mock(return_value=[detection])):
                state = engine.run_cycle(cfg, send=False)
            return state, wms, geo

    def test_european_surge_at_known_facility_creates_unusual_incident(self):
        state, wms, geo = self.cycle(200.0)
        self.assertEqual(len(state.incidents), 1)
        incident = state.incidents[0]
        self.assertEqual(incident["kind"], "unusual")
        self.assertEqual(incident["max_frp"], 200.0)
        self.assertEqual(incident["typical_frp"], 10.0)
        self.assertEqual(incident["priority"], "high")
        self.assertEqual(state.memory[Memory.key(self.LAT, self.LON)]["2026-11-07"], 200.0)
        wms.feature_info.assert_called_once()
        geo.facility_within.assert_called_once()

    def test_ordinary_facility_heat_stays_suppressed(self):
        state, wms, _ = self.cycle(10.0)
        self.assertEqual(state.incidents, [])
        self.assertEqual(state.memory[Memory.key(self.LAT, self.LON)]["2026-11-07"], 10.0)
        wms.feature_info.assert_called_once()

    def test_known_heat_away_from_facilities_needs_no_strength_query(self):
        state, wms, _ = self.cycle(200.0, at_facility=False)
        self.assertEqual(state.incidents, [])
        wms.feature_info.assert_not_called()

    def test_missing_strength_does_not_create_a_spurious_surge(self):
        state, wms, _ = self.cycle(None)
        self.assertEqual(state.incidents, [])
        wms.feature_info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
