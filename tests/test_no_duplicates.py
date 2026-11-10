"""Regressions: one fire is one record, however often its symbol comes back.

Found on 10 Nov 2026: symbols waiting for their strength were judged again on every check for
24 hours. Each check added them to their incident once more, and once the incident was older
than 12 hours each check created another record under the same id (up to 288 copies). A
recovered past event was also split into one record per picture.
"""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rasad import engine, geocollect
from rasad.brain import add_to_incident, find_incident, new_incident
from rasad.util import iso, load_json, save_json


def _cfg(folder):
    cfg = copy.deepcopy(engine.DEFAULTS)
    cfg["_state_dir"] = Path(folder) / "state"
    cfg["_site_dir"] = Path(folder) / "site"
    cfg["_geodata_dir"] = Path(folder) / "geo"
    cfg["regions"] = [{"id": "test", "name": "إيران", "bbox": [55, 29, 57, 31]}]
    cfg["seawatch"]["enabled"] = False
    cfg["clip"]["enabled"] = False
    cfg["recovery"]["enabled"] = False
    cfg["eumetview"]["fire_layers"] = cfg["eumetview"]["fire_layers"][:1]
    cfg["eumetview"]["lookback_slots"] = 1
    cfg["eumetview"]["catchup_hours"] = 0
    return cfg


def _symbol(src, when, lat=30.0567, lon=56.1395, fi=True):
    det = {"src": src, "family": "msg_iodc" if src == "iodc_fire" else "mtg", "kind": "geo",
           "t": iso(when), "lat": lat, "lon": lon, "frp": None}
    if fi:
        det["_fi"] = ["layer", [55, 29, 57, 31], 10, 10, 5, 5, iso(when)]
    return det


class StrengthQueueTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc)

    def run_checks(self, pending, incidents, feature_info, checks=3):
        """Run several checks on a memory holding `incidents` and `pending` symbols."""
        with tempfile.TemporaryDirectory() as folder:
            cfg = _cfg(folder)
            save_json(cfg["_state_dir"] / "state.json",
                      {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                       "sea_check": engine.SEA_CHECK, "tidy": engine.TIDY,
                       "strength_pending": {f"k{n}": d for n, d in enumerate(pending)}})
            save_json(cfg["_state_dir"] / "incidents.json", incidents)
            wms = Mock()
            wms.feature_info.return_value = feature_info
            geo = Mock(version="test")
            geo.facility_within.return_value = None
            geo.facility.return_value = None
            geo.place_kind.return_value = "land"
            geo.describe.return_value = ("إيران", "إيران")
            for k in range(checks):
                now = self.NOW + dt.timedelta(minutes=3 * k)
                with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(),
                                    WMS=Mock(return_value=wms), Geo=Mock(return_value=geo),
                                    Availability=Mock(), latest_time=Mock(return_value=None),
                                    detect_geo_layer=Mock(return_value=[])):
                    engine.run_cycle(cfg, send=False)
            return (load_json(cfg["_state_dir"] / "incidents.json", []),
                    load_json(cfg["_state_dir"] / "state.json", {}), wms)

    def old_incident(self, det):
        inc = new_incident(dict(det), "new")
        add_to_incident(inc, {k: v for k, v in det.items() if k != "_fi"})
        return inc

    def test_symbol_without_strength_is_not_judged_again(self):
        seen = self.NOW - dt.timedelta(hours=14)   # its incident is older than the 12-hour window
        det = _symbol("mtg_frp", seen)
        incidents, state, _ = self.run_checks([det], [self.old_incident(det)], feature_info={})
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["n"], 1)
        self.assertEqual(len(state["strength_pending"]), 1)   # still waiting, asked again later

    def test_second_satellite_symbols_are_never_queued_for_strength(self):
        det = _symbol("iodc_fire", self.NOW - dt.timedelta(hours=14))
        incidents, state, wms = self.run_checks([det], [self.old_incident(det)], feature_info={"frp": 9})
        self.assertEqual(len(incidents), 1)
        self.assertEqual(state["strength_pending"], {})
        wms.feature_info.assert_not_called()

    def test_late_strength_joins_the_old_incident(self):
        seen = self.NOW - dt.timedelta(hours=14)
        det = _symbol("mtg_frp", seen)
        incidents, state, _ = self.run_checks([det], [self.old_incident(det)], feature_info={"frp": 42.0})
        self.assertEqual(len(incidents), 1)
        inc = incidents[0]
        self.assertEqual(inc["n"], 1)
        self.assertEqual(inc["max_frp"], 42.0)
        self.assertEqual(inc["frp_series"], [[iso(seen), 42.0, "mtg"]])
        self.assertEqual(state["strength_pending"], {})


class MatchingTests(unittest.TestCase):
    def test_detection_in_the_middle_of_a_long_fire_belongs_to_it(self):
        start = dt.datetime(2026, 11, 8, 0, tzinfo=dt.timezone.utc)
        inc = new_incident(_symbol("mtg_frp", start, fi=False), "new")
        add_to_incident(inc, _symbol("mtg_frp", start, fi=False))
        add_to_incident(inc, _symbol("mtg_frp", start + dt.timedelta(hours=30), fi=False))
        middle = _symbol("mtg_frp", start + dt.timedelta(hours=15), fi=False)
        self.assertIs(find_incident([inc], middle, 6, 12), inc)

    def test_same_symbol_twice_is_counted_once(self):
        when = dt.datetime(2026, 11, 8, 0, tzinfo=dt.timezone.utc)
        inc = new_incident(_symbol("mtg_frp", when, fi=False), "new")
        add_to_incident(inc, _symbol("mtg_frp", when, fi=False))
        add_to_incident(inc, _symbol("mtg_frp", when, fi=False))
        self.assertEqual(inc["n"], 1)
        self.assertEqual(len(inc["observations"]), 1)


class TidyTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 10, 7, tzinfo=dt.timezone.utc)

    def test_copies_under_one_id_are_merged_into_the_sent_one(self):
        when = self.NOW - dt.timedelta(hours=20)
        first = new_incident(_symbol("mtg_frp", when, fi=False), "new")
        add_to_incident(first, _symbol("mtg_frp", when, fi=False))
        copies = [copy.deepcopy(first) for _ in range(5)]
        copies[2]["alerted_at"] = iso(when)
        copies[2]["tg_msg"] = 77
        S = Mock(incidents=copies, state={"tidy": engine.TIDY})
        engine.tidy_incidents(S, engine.DEFAULTS, self.NOW)
        self.assertEqual(len(S.incidents), 1)
        self.assertEqual(S.incidents[0]["tg_msg"], 77)
        self.assertEqual(S.incidents[0]["n"], 1)

    def test_recovered_pieces_of_one_event_become_one(self):
        pieces = []
        for k, (lat, lon) in enumerate([(25.9354, 49.7018), (25.925, 49.7136), (25.9239, 49.6933)]):
            when = self.NOW - dt.timedelta(days=2) + dt.timedelta(minutes=10 * k)
            det = _symbol("mtg_frp", when, lat, lon, fi=False)
            det["frp"] = 40.0 + k
            inc = new_incident(dict(det), "unusual")
            add_to_incident(inc, det)
            inc["recovered_historical"] = True
            pieces.append(inc)
        S = Mock(incidents=pieces, state={})
        with patch.object(engine, "finalize"):
            engine.tidy_incidents(S, engine.DEFAULTS, self.NOW)
        self.assertEqual(len(S.incidents), 1)
        self.assertEqual(len(S.incidents[0]["geo_slots"]), 3)
        self.assertEqual(S.incidents[0]["max_frp"], 42.0)


class ScanOrderTests(unittest.TestCase):
    def test_unread_tiles_come_before_second_reads(self):
        now = dt.datetime(2026, 11, 10, 7, 0, tzinfo=dt.timezone.utc)
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["eumetview"]["fire_layers"] = cfg["eumetview"]["fire_layers"][:1]
        cfg["eumetview"].update(scan_max_images=2, scan_workers=1, lookback_slots=2, catchup_hours=0)
        layer = cfg["eumetview"]["fire_layers"][0]
        tiles = [[0, 0, 1, 1], [1, 0, 2, 1]]
        newest = now - dt.timedelta(minutes=14)
        older = newest - dt.timedelta(minutes=10)
        # the older slot was read young (needs a settled second read); the newest is unread
        state = {"slots": {geocollect._key(layer, tile, older): 5.0 for tile in tiles}}
        asked = []

        def detect(wms, L, tile, when):
            asked.append(when)
            return []

        class Avail:
            def has(self, *a, **k):
                return True

        geocollect.collect(cfg, object(), state, now, tiles, Avail(), detect=detect,
                           latest=lambda *a, **k: newest)
        self.assertEqual(asked, [newest, newest])


if __name__ == "__main__":
    unittest.main()


class VideoRetryTests(unittest.TestCase):
    """A fire that never shows in the pictures must not be rendered again every 10 minutes."""

    def test_failed_renders_wait_longer_each_time(self):
        from rasad import mediaqueue
        done = dt.datetime(2026, 11, 10, 7, tzinfo=dt.timezone.utc)
        cfg = {"clip": {"initial_retry_min": 10}}
        job = {"refresh": False}

        def due(attempts, minutes):
            result = {"ok": False, "attempts": attempts, "finished_at": iso(done)}
            return mediaqueue._retry_due(cfg, job, result, done + dt.timedelta(minutes=minutes))

        self.assertTrue(due(1, 10))
        self.assertFalse(due(2, 15))
        self.assertTrue(due(2, 20))
        self.assertFalse(due(4, 60))
        self.assertTrue(due(4, 80))
        self.assertFalse(due(10, 170))
        self.assertTrue(due(10, 180))   # never more than three hours apart
