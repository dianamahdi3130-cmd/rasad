"""Warm exact finder rasters independently of the scanner's short cycle.

Only the validated picture cache is written. Finding spots, recording scan
completion, incident decisions and notifications remain the scanner's work.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import datetime as dt
import fcntl
import math
from pathlib import Path
import time

from .seawatch import Pictures, box_size, tile_requests
from .util import floor_time, iso, log, make_session, sun_elevation, utcnow
from .wms import WMS


class Collector:
    def __init__(self, cfg, wms, clock=None):
        self.cfg, self.wms = cfg, wms
        self.clock = clock or time.monotonic
        self.cache_dir = Path(cfg["_state_dir"]).resolve().parent / ".rasad-pictures"
        self.failures, self.validated = {}, {}
        self.cursor = 0

    @staticmethod
    def key(request):
        layer, bbox, width, height, when = request
        return layer, tuple(bbox), width, height, iso(when)

    def cached(self, pics, request):
        key = self.key(request)
        path = pics._disk_path(key)
        try:
            stat = path.stat()
            if path.is_symlink() or time.time() - stat.st_mtime > Pictures.CACHE_TTL_S:
                return False
            signature = stat.st_mtime_ns, stat.st_size
            if self.validated.get(key) == signature:
                return True
            if pics._read_disk(key) is not None:
                self.validated[key] = signature
                return True
        except OSError:
            pass
        self.validated.pop(key, None)
        return False

    def comparisons(self, now):
        """Recent comparisons first; times and dimensions match the finder."""
        C = self.cfg["seawatch"]
        D = C.get("daytime", {})
        cad, km = int(C["cadence_min"]), float(C["km_per_px"])
        day_km = float(D.get("km_per_px", km))   # the day finder keeps its own picture detail
        end = floor_time(now - dt.timedelta(minutes=max(0, float(C.get("delay_min", 0)))), cad)
        count = max(1, min(36, math.ceil(float(C.get("catchup_hours", 3)) * 60 / cad) + 1))
        boxes = [box for box in C.get("boxes", []) if box.get("enabled", True)]
        for k in range(count):
            when = end - dt.timedelta(minutes=k * cad)
            for index, box in enumerate(boxes):
                w, s, e, n = box["bbox"]
                elevations = [sun_elevation(lat, lon, when) for lat in (s, n) for lon in (w, e)]
                day = D.get("enabled") and max(elevations) >= float(D.get("min_sun_elev", -6))
                night = min(elevations) <= float(C.get("sun_max_elev", -6))
                refs = [int(m) for m in (D.get("ref_minutes", [30, 60]) if day else C["ref_minutes"])]
                if not (day or night) or not refs or any(m <= 0 for m in refs):
                    continue
                if day and len(set(refs)) < 2:
                    continue
                layers = [C["layer"], C["check_layer"]] if day else [C["layer"]]
                width, height = box_size(box, day_km if day else km)
                # a big full-detail picture is cached as tiles, exactly as the finder reads it
                requests = [part for m in [0] + list(dict.fromkeys(refs)) for layer in layers
                            for part, _ in tile_requests(layer, tuple(box["bbox"]), width, height,
                                                         when - dt.timedelta(minutes=m))]
                yield index, when, requests

    def process_batch(self, now=None):
        """Attempt at most four rasters, using at most two network connections."""
        C = self.cfg["seawatch"]
        P = C.get("prefetch", {})
        if not C.get("enabled") or not P.get("enabled", True):
            return {"requested": 0, "cached": 0, "failed": 0}
        now = now or utcnow()
        pics = Pictures(self.wms, cache_dir=self.cache_dir, attempts=1)
        if pics.cache_dir is None:
            return {"requested": 0, "cached": 0, "failed": 0}
        groups, live = [], set()
        owned = [path for path in self.cache_dir.glob("*.png")
                 if Pictures._CACHE_NAME.fullmatch(path.name)]
        try:
            pressure = sum(p.stat().st_size for p in owned if p.is_file()) >= Pictures.CACHE_MAX_BYTES * .8
        except FileNotFoundError:
            pressure = False
        for index, when, requests in list(self.comparisons(now))[:64]:
            live.update(self.key(r) for r in requests)
            recent = now - when <= dt.timedelta(minutes=40)
            if pressure and not recent:
                continue  # do not evict fresh comparisons while filling the older catch-up tail
            missing = [r for r in requests if not self.cached(pics, r)]
            due = [r for r in missing if self.clock() >= self.failures.get(self.key(r), (0, 0))[1]]
            if not due:
                continue
            completed = len(requests) - len(missing)
            # Complete partial comparisons first, fairly across equal-age regions.
            score = (not recent, completed == 0, -completed / len(requests),
                     -when.timestamp(), (index - self.cursor) % max(1, len(C.get("boxes", []))))
            groups.append((score, index, due))
        self.failures = {key: value for key, value in self.failures.items() if key in live}
        self.validated = {key: value for key, value in self.validated.items() if key in live}
        if not groups:
            return {"requested": 0, "cached": 0, "failed": 0}
        _, index, due = min(groups, key=lambda group: group[0])
        self.cursor = index + 1
        batch = due[:max(1, min(12, int(P.get("batch_size", 4))))]
        budget = max(1.0, min(120.0, float(P.get("budget_s", 90))))
        timeout = max(.5, min(45.0, float(P.get("request_timeout_s", 40))))
        guard = getattr(self.wms, "budget", None)
        with self.wms.budget(budget, timeout_s=timeout) if callable(guard) else nullcontext():
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda request: pics.get(*request, channel=0), batch))
        ready = 0
        for request in batch:
            key = self.key(request)
            if self.cached(pics, request):
                ready += 1
                self.failures.pop(key, None)
            else:
                attempts = min(6, self.failures.get(key, (0, 0))[0] + 1)
                self.failures[key] = attempts, self.clock() + min(600, 30 * 2 ** (attempts - 1))
        pics.cache.clear()
        return {"requested": len(batch), "cached": ready, "failed": len(batch) - ready}


def run_worker(cfg, interval=5.0, max_minutes=340.0, once=False):
    """Keep caching useful rasters while scans and video processing run separately."""
    if not cfg["seawatch"].get("enabled") or not cfg["seawatch"].get("prefetch", {}).get("enabled", True):
        return
    cache = Path(cfg["_state_dir"]).resolve().parent / ".rasad-pictures"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".prefetch.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("picture prefetch worker already running")
            return
        with make_session() as session:
            collector = Collector(cfg, WMS(cfg["eumetview"]["wms_url"], session))
            stop_at = time.monotonic() + max(0.0, float(max_minutes)) * 60
            while once or time.monotonic() < stop_at:
                try:
                    result = collector.process_batch()
                    if result["requested"]:
                        log.info("picture prefetch: %d cached, %d pending", result["cached"], result["failed"])
                except Exception:  # noqa: BLE001
                    log.exception("picture prefetch failed; scans retain ownership of source state")
                if once:
                    return
                time.sleep(max(.1, float(interval)))
