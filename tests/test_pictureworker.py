"""Slow or incomplete picture sources must not starve other useful comparisons."""
import copy
from contextlib import contextmanager
import datetime as dt
import fcntl
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from rasad import engine, pictureworker, seawatch
from rasad.util import UTC


class RasterService:
    url = "https://example.test/wms"

    def __init__(self):
        self.calls = []
        self.missing = lambda layer, when: False
        self.substituted = False
        self.transparent = False
        self.bounds = []
        self.lock = threading.Lock()
        self.active = self.peak = 0

    @contextmanager
    def budget(self, seconds, timeout_s=20):
        self.bounds.append((seconds, timeout_s))
        yield self

    def getmap(self, layer, bbox, width, height, *, t, **kwargs):
        with self.lock:
            self.calls.append((layer, tuple(bbox), width, height, t))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(.002)
            if self.missing(layer, t):
                return None, None
            raw = np.full((height, width, 4), [80, 20, 5, 255], dtype=np.uint8)
            if self.transparent:
                raw[0, 0, 3] = 0
            actual = t - dt.timedelta(minutes=10) if self.substituted else t
            return Image.fromarray(raw), actual
        finally:
            with self.lock:
                self.active -= 1


class PictureWorkerTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 14, 7, 0, tzinfo=UTC)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=self.root / "state", _site_dir=self.root / "site")
        self.cfg["seawatch"].update(layer="fire", check_layer="ir", delay_min=0, catchup_hours=0,
                                    boxes=[{"id": "first", "bbox": [54, 25, 54.1, 25.1]}])
        self.cfg["seawatch"]["prefetch"] = {}  # exercise the worker's own fallback settings
        self.cfg["seawatch"]["daytime"].update(enabled=True, ref_minutes=[30, 60])
        self.clock = [100.0]
        self.service = RasterService()
        self.collector = pictureworker.Collector(self.cfg, self.service, lambda: self.clock[0])
        daylight = patch.object(pictureworker, "sun_elevation", return_value=40)
        daylight.start()
        self.addCleanup(daylight.stop)

    def batch(self):
        return self.collector.process_batch(self.NOW)

    def test_six_required_rasters_are_cached_without_writing_scanner_state(self):
        sentinels = [self.root / "state" / name for name in
                     ("state.json", "incidents.json", "memory.json")]
        sentinels.append(self.root / "site" / "data" / "meta.json")
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing scanner state")
        self.assertEqual(self.batch(), {"requested": 4, "cached": 4, "failed": 0})
        self.assertEqual(self.batch(), {"requested": 2, "cached": 2, "failed": 0})
        self.assertEqual(self.batch()["requested"], 0)
        self.assertEqual(len(self.service.calls), 6)
        self.assertTrue(all(path.read_bytes() == b"existing scanner state" for path in sentinels))
        self.assertLessEqual(self.service.peak, 2)
        self.assertEqual(self.service.bounds, [(90.0, 40.0), (90.0, 40.0)])

    def test_slow_thirty_second_source_can_complete_outside_the_scanner_budget(self):
        # Real full-grid source responses took about 30 seconds. Simulate that
        # duration without sleeping, enforcing both request and batch budgets.
        clock = self.clock

        class SlowService(RasterService):
            @contextmanager
            def budget(self, seconds, timeout_s=20):
                self.bounds.append((seconds, timeout_s))
                self.deadline, self.request_timeout = clock[0] + seconds, timeout_s
                yield self

            def getmap(self, layer, bbox, width, height, *, t, **kwargs):
                duration = 30 if layer == "fire" else 1
                with self.lock:
                    if duration > self.request_timeout or clock[0] + duration > self.deadline:
                        self.calls.append((layer, tuple(bbox), width, height, t))
                        return None, None
                    clock[0] += duration
                return super().getmap(layer, bbox, width, height, t=t, **kwargs)

        short_cfg = copy.deepcopy(self.cfg)
        short_cfg["_state_dir"] = self.root / "short-budget" / "state"
        short_cfg["seawatch"]["prefetch"] = {"budget_s": 22, "request_timeout_s": 6}
        short = pictureworker.Collector(short_cfg, SlowService(), lambda: clock[0])
        self.assertEqual(short.process_batch(self.NOW)["failed"], 2,
                         "Scanner-sized timeouts cannot fetch the slow RGB channel")
        source = SlowService()
        self.collector = pictureworker.Collector(self.cfg, source, lambda: clock[0])
        self.assertEqual(self.batch(), {"requested": 4, "cached": 4, "failed": 0})
        self.assertEqual(self.batch(), {"requested": 2, "cached": 2, "failed": 0})
        self.assertEqual(self.batch()["requested"], 0)
        self.assertEqual(len(source.calls), 6)
        self.assertEqual(source.bounds, [(90.0, 40.0), (90.0, 40.0)])

    def test_exact_detector_grid_timestamps_and_channels_are_preserved(self):
        self.batch()
        self.batch()
        box = self.cfg["seawatch"]["boxes"][0]
        width, height = seawatch.box_size(box, .5)
        expected = {(layer, tuple(box["bbox"]), width, height, self.NOW - dt.timedelta(minutes=m))
                    for layer in ("fire", "ir") for m in (0, 30, 60)}
        self.assertEqual(set(self.service.calls), expected)
        self.assertTrue(all(row[-1] <= self.NOW for row in self.service.calls))

    def test_scanner_can_read_other_channels_from_the_same_cached_raster(self):
        self.batch()
        self.batch()
        box = self.cfg["seawatch"]["boxes"][0]
        width, height = seawatch.box_size(box, .5)
        unavailable = Mock(url=self.service.url)
        unavailable.getmap.side_effect = AssertionError("Cached raster must not need a network call")
        pics = seawatch.Pictures(unavailable, cache_dir=self.collector.cache_dir)
        grey = pics.get("fire", box["bbox"], width, height, self.NOW)
        red = pics.get("fire", box["bbox"], width, height, self.NOW, channel=0)
        self.assertEqual(float(grey[0, 0]), 35)
        self.assertEqual(float(red[0, 0]), 80)
        unavailable.getmap.assert_not_called()

    def test_restart_reuses_validated_disk_cache(self):
        self.batch()
        self.batch()
        restarted = pictureworker.Collector(self.cfg, self.service)
        self.assertEqual(restarted.process_batch(self.NOW)["requested"], 0)
        self.assertEqual(len(self.service.calls), 6)

    def test_partial_comparison_finishes_when_the_latest_clock_advances(self):
        self.assertEqual(self.batch()["cached"], 4)
        self.cfg["seawatch"]["catchup_hours"] = 1
        self.NOW += dt.timedelta(minutes=10)
        completed = self.batch()
        self.assertEqual(completed["requested"], 2)
        self.assertEqual({row[-1] for row in self.service.calls[-2:]},
                         {self.NOW - dt.timedelta(minutes=70)})

    def test_cache_pressure_preserves_recent_work_instead_of_filling_old_tail(self):
        self.cfg["seawatch"]["catchup_hours"] = 1
        for _, when, requests in self.collector.comparisons(self.NOW):
            if self.NOW - when <= dt.timedelta(minutes=40):
                for request in requests:
                    self.collector.failures[self.collector.key(request)] = (1, self.clock[0] + 30)
        self.collector.cache_dir.mkdir(parents=True)
        other_grid = self.collector.cache_dir / ("f" * 64 + ".png")
        other_grid.write_bytes(b"x" * 850)
        with patch.object(seawatch.Pictures, "CACHE_MAX_BYTES", 1000):
            self.assertEqual(self.batch()["requested"], 0)
            self.assertEqual(self.service.calls, [])
        other_grid.unlink()
        self.assertGreater(self.batch()["requested"], 0)

    def test_unavailable_latest_slot_does_not_block_older_comparison(self):
        self.cfg["seawatch"]["catchup_hours"] = .2
        self.service.missing = lambda layer, when: when == self.NOW
        for _ in range(4):
            self.batch()
        older = self.NOW - dt.timedelta(minutes=10)
        self.assertTrue(any(row[-1] == older for row in self.service.calls))
        self.assertEqual(sum(row[-1] == self.NOW for row in self.service.calls), 2,
                         "Missing current channels should back off while older work proceeds")

    def test_the_worker_waits_while_a_check_reads_the_official_products(self):
        # 15 Nov 2026, live: with ten sea boxes at full detail the worker never rested, and the
        # check's own reads of the official fire products (what alerts on land wait for) fell behind
        from rasad import util
        util.official_reads(self.cfg, 60)
        self.assertGreater(util.official_reads_left(self.cfg), 50)
        self.assertEqual(self.batch()["requested"], 0, "nothing is asked of the server meanwhile")
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.collector.failures, {}, "and what was not asked for has not failed")
        util.official_reads_done(self.cfg)
        self.assertEqual(util.official_reads_left(self.cfg), 0)
        self.assertEqual(self.batch()["cached"], 4)

    def test_a_batch_stops_between_pairs_when_a_check_starts_reading(self):
        from rasad import util
        asked = []

        def getmap(layer, bbox, width, height, *, t, **kwargs):
            asked.append(t)
            if len(asked) == 2:                       # the check starts while the first pair is on its way
                util.official_reads(self.cfg, 60)
            return RasterService.getmap(self.service, layer, bbox, width, height, t=t, **kwargs)
        with patch.object(self.service, "getmap", side_effect=getmap):
            result = self.batch()
        self.assertEqual((result["requested"], result["cached"], result["failed"]), (2, 2, 0))
        self.assertEqual(len(asked), 2, "the second pair waits its turn")

    def test_a_note_left_by_a_check_that_died_ends_by_itself(self):
        from rasad import util
        util.official_reads(self.cfg, 0.05)
        time.sleep(.08)
        self.assertEqual(util.official_reads_left(self.cfg), 0)
        util.official_reads(self.cfg, 10_000)
        self.assertLessEqual(util.official_reads_left(self.cfg), 180, "never longer than three minutes")
        self.assertEqual(util.official_reads_left({}), 0, "no folder to look in: go ahead")

    def test_failed_rasters_back_off_then_retry_without_poisoning_cache(self):
        self.service.missing = lambda layer, when: True
        self.assertEqual(self.batch()["failed"], 4)
        self.assertEqual(self.batch()["failed"], 2)
        self.assertEqual(self.batch()["requested"], 0)
        self.clock[0] += 31
        self.assertEqual(self.batch()["failed"], 4)
        self.assertEqual(len(self.service.calls), 10)
        self.assertEqual(list(self.collector.cache_dir.glob("*.png")), [])

    def test_substituted_or_incomplete_images_never_become_ready(self):
        for mode in ("substituted", "transparent"):
            with self.subTest(mode=mode):
                self.collector.failures.clear()
                setattr(self.service, mode, True)
                result = self.batch()
                self.assertEqual(result["cached"], 0)
                self.assertEqual(result["failed"], 4)
                self.assertEqual(list(self.collector.cache_dir.glob("*.png")), [])
                setattr(self.service, mode, False)

    def test_each_enabled_region_gets_work(self):
        self.cfg["seawatch"]["boxes"].append({"id": "second", "bbox": [55, 25, 55.1, 25.1]})
        self.cfg["seawatch"]["boxes"].append({"id": "off", "bbox": [56, 25, 56.1, 25.1], "enabled": False})
        for _ in range(4):
            self.batch()
        boxes = {row[1] for row in self.service.calls}
        self.assertEqual(boxes, {(54, 25, 54.1, 25.1), (55, 25, 55.1, 25.1)})

    def test_night_fetches_only_required_fire_comparison(self):
        with patch.object(pictureworker, "sun_elevation", return_value=-30):
            result = self.batch()
        # the night finder compares with the pictures 30, 60 and 90 minutes before
        self.assertEqual(result, {"requested": 4, "cached": 4, "failed": 0})
        self.assertEqual({row[0] for row in self.service.calls}, {"fire"})
        self.assertEqual({row[-1] for row in self.service.calls},
                         {self.NOW - dt.timedelta(minutes=m) for m in (0, 30, 60, 90)})

    def test_disabled_prefetch_does_no_network_or_state_work(self):
        self.cfg["seawatch"]["prefetch"] = {"enabled": False}
        self.assertEqual(self.batch()["requested"], 0)
        self.assertEqual(self.service.calls, [])
        self.assertFalse(self.collector.cache_dir.exists())

    def test_second_worker_exits_when_cache_lock_is_owned(self):
        self.collector.cache_dir.mkdir(parents=True)
        with (self.collector.cache_dir / ".prefetch.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(pictureworker, "make_session") as session:
                pictureworker.run_worker(self.cfg, once=True)
            session.assert_not_called()

    def test_cli_routes_to_read_only_worker(self):
        from rasad import main
        with patch.object(main, "load_config", return_value=self.cfg), \
                patch.object(pictureworker, "run_worker") as worker:
            main.main(["picture-worker", "--once", "--interval", "2", "--max-minutes", "3"])
        worker.assert_called_once_with(self.cfg, 2, 3, True)


if __name__ == "__main__":
    unittest.main()
