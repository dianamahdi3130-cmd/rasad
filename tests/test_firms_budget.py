"""FIRMS deadlines, partial availability and truthful measurements without network access."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from rasad.sources import firms_fetch


HEADER = "latitude,longitude,acq_date,acq_time,frp,confidence,daynight,scan,track,instrument,satellite\n"
ROW = "26.1234,56.4321,2026-11-08,1702,47.3,n,N,0.4,0.5,VIIRS,N21\n"


class Clock:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class FirmsBudgetTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"firms": {"map_key": "SECRET-KEY", "base_url": "https://example.invalid",
                              "sources": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"]}}
        self.bbox = [50, 24, 60, 30]
        self.session = Mock()
        self.session.get.return_value = SimpleNamespace(status_code=200, text=HEADER + ROW)

    def fetch(self, status=None, cfg=None):
        return firms_fetch(self.session, cfg or self.cfg, self.bbox, 2, status=status)

    def test_waves_never_start_after_budget_and_timeout_uses_remaining_time(self):
        self.cfg["firms"]["max_workers"] = 1
        clock = Clock()
        timeouts = []

        def request(url, timeout):
            timeouts.append(timeout)
            clock.advance(18)
            return SimpleNamespace(status_code=200, text=HEADER + ROW)

        self.session.get.side_effect = request
        status = {}
        with patch("rasad.sources.time.monotonic", side_effect=clock):
            rows = self.fetch(status)
        self.assertEqual(timeouts, [20, 7])
        self.assertEqual(len(rows), 2)
        self.assertEqual(status, {"attempted": 2, "completed": 2, "failed": 0,
                                  "deferred": 2, "ok": False})

    def test_rotation_gives_deferred_sources_the_next_turn(self):
        self.cfg["firms"].update(max_workers=1, source_offset=2)
        clock = Clock()

        def request(url, timeout):
            clock.advance(18)
            return SimpleNamespace(status_code=200, text=HEADER + ROW)

        self.session.get.side_effect = request
        with patch("rasad.sources.time.monotonic", side_effect=clock):
            rows = self.fetch()
        self.assertEqual([d["src"] for d in rows], ["VIIRS_SNPP_NRT", "MODIS_NRT"])

    def test_at_most_two_requests_are_active_and_rows_keep_source_order(self):
        lock, barrier = threading.Lock(), threading.Barrier(2)
        active = peak = 0

        def request(url, timeout):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=2)
            with lock:
                active -= 1
            return SimpleNamespace(status_code=200, text=HEADER + ROW)

        self.session.get.side_effect = request
        rows = self.fetch()
        self.assertEqual(peak, 2)
        self.assertEqual([d["src"] for d in rows], self.cfg["firms"]["sources"])

    def test_valid_empty_csv_is_completed_healthy_data(self):
        self.session.get.return_value = SimpleNamespace(status_code=200, text=HEADER)
        status = {}
        self.assertEqual(self.fetch(status), [])
        self.assertEqual(status, {"attempted": 4, "completed": 4, "failed": 0,
                                  "deferred": 0, "ok": True})

    def test_failures_and_invalid_responses_are_not_healthy_empty_reads(self):
        for response in (SimpleNamespace(status_code=429, text="SECRET-KEY retry"),
                         SimpleNamespace(status_code=200, text="latitude,error\nSECRET-KEY,bad")):
            with self.subTest(response=response):
                self.session.get.return_value = response
                status = {}
                with self.assertLogs("rasad", level="WARNING") as logs:
                    self.assertEqual(self.fetch(status), [])
                self.assertEqual(status["failed"], 4)
                self.assertFalse(status["ok"])
                self.assertNotIn("SECRET-KEY", "\n".join(logs.output))

    def test_exception_logs_never_expose_the_key_in_request_urls(self):
        self.session.get.side_effect = requests.Timeout("GET https://example.invalid/SECRET-KEY/ timed out")
        status = {}
        with self.assertLogs("rasad", level="WARNING") as logs:
            self.assertEqual(self.fetch(status), [])
        self.assertEqual(status["failed"], 4)
        self.assertFalse(status["ok"])
        self.assertNotIn("SECRET-KEY", "\n".join(logs.output))

    def test_missing_key_and_zero_budget_make_no_requests(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["firms"]["map_key"] = ""
        status = {}
        self.assertEqual(self.fetch(status, cfg), [])
        self.assertEqual(status["attempted"], 0)
        self.assertFalse(status["ok"])
        self.cfg["firms"]["budget_s"] = 0
        self.assertEqual(self.fetch(status), [])
        self.assertEqual(status["deferred"], 4)
        self.session.get.assert_not_called()

    def test_nonfinite_and_negative_power_are_unknown_not_fabricated_values(self):
        self.cfg["firms"]["sources"] = ["VIIRS_NOAA21_NRT"]
        for value in ("nan", "inf", "-1", "", "bad"):
            with self.subTest(value=value):
                self.session.get.return_value = SimpleNamespace(status_code=200,
                    text=HEADER + ROW.replace(",47.3,", f",{value},"))
                detection = self.fetch()[0]
                self.assertIsNone(detection["frp"])
                self.assertEqual(detection["t"], "2026-11-08T17:02:00Z")

    def test_position_and_footprint_metadata_are_preserved_only_when_valid(self):
        self.cfg["firms"]["sources"] = ["VIIRS_NOAA21_NRT"]
        detection = self.fetch()[0]
        self.assertEqual(detection["family"], "viirs")
        self.assertEqual((detection["lat"], detection["lon"], detection["frp"]),
                         (26.1234, 56.4321, 47.3))
        self.assertEqual((detection["scan"], detection["track"], detection["instrument"],
                          detection["satellite"]), (0.4, 0.5, "VIIRS", "N21"))
        self.session.get.return_value = SimpleNamespace(status_code=200,
            text=HEADER + ROW.replace(",0.4,0.5,", ",-1,inf,"))
        detection = self.fetch()[0]
        self.assertNotIn("scan", detection)
        self.assertNotIn("track", detection)
        self.session.get.return_value = SimpleNamespace(status_code=200,
            text=HEADER + ROW.replace("26.1234", "nan"))
        self.assertEqual(self.fetch(), [])


if __name__ == "__main__":
    unittest.main()
