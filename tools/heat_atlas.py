"""Build the long-term map of usual heat (rasad/data/heat_atlas.json.gz) from NOAA's public VIIRS
fire files (NOAA-20 and NOAA-21, AWS, no account; the archive starts in March 2024).

For sampled dates (every `--step` days), every granule of the day and night passes over Rasad's
watched box is read, but only its fire-pixel lists: HTTP range requests of a few kilobytes instead
of the whole 1-2.5 MB file. Each pixel marks its ~500 m cell (0.005 degrees) as hot on that date.
The atlas keeps, per cell, on how many sampled dates it was hot (cells hot on 2 dates or more).

What it is for (rasad/patterns.py): heat at a spot of a plant that rarely burns, against its usual
flares; how often a place burns over years, not only over Rasad's month of memory.

Run (about half an hour; needs AWS):  python tools/heat_atlas.py --start 2024-03-05 --step 5
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import io
import json
import math
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import h5py
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rasad import noaafire  # noqa: E402

BUCKETS = {"n20": "noaa-nesdis-n20-pds", "n21": "noaa-nesdis-n21-pds"}
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
CELL = 0.005
BOX = (32.0, 12.0, 60.0, 37.4)           # the union of config.yaml's regions
# the passes over the box: by day about 08-12 UTC, by night about 20-24 UTC (and just after)
HOURS = set(range(7, 13)) | set(range(19, 24)) | {0}
_local = threading.local()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=2))
    return _local.s


def get(url, rng=None, timeout=40):
    for attempt in range(4):
        try:
            r = session().get(url, headers={"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else None, timeout=timeout)
            if r.status_code in (200, 206):
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed {url} {rng}")


class RangeFile(io.RawIOBase):
    """A remote file read in 64 KB blocks on demand (h5py reads only what it needs)."""
    BLOCK = 65536

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.blocks = url, size, 0, {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def _block(self, i):
        if i not in self.blocks:
            lo = i * self.BLOCK
            self.blocks[i] = get(self.url, (lo, min(self.size, lo + self.BLOCK) - 1))
        return self.blocks[i]

    def readinto(self, b):
        n = min(len(b), max(0, self.size - self.pos))
        out, got = memoryview(b), 0
        while got < n:
            i, off = divmod(self.pos + got, self.BLOCK)
            chunk = self._block(i)[off:off + n - got]
            if not chunk:
                break
            out[got:got + len(chunk)] = chunk
            got += len(chunk)
        self.pos += got
        return got


def listing(bucket, day):
    prefix = f"{noaafire.PRODUCT}/{day:%Y/%m/%d}/"
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(get(f"https://{bucket}.s3.amazonaws.com/?" + "&".join(f"{k}={v}" for k, v in params.items())))
        for c in root.findall(NS + "Contents"):
            out.append((c.find(NS + "Key").text, int(c.find(NS + "Size").text)))
        t = root.find(NS + "NextContinuationToken")
        if t is None:
            return out
        token = requests.utils.quote(t.text, safe="")


def touches(bucket, key):
    poly = noaafire.outline(get(f"https://{bucket}.s3.amazonaws.com/{key}", (0, 32767)))
    return bool(poly) and noaafire.bounds_touch(poly, BOX)


def pixels(bucket, key, size):
    f = RangeFile(f"https://{bucket}.s3.amazonaws.com/{key}", size)
    with h5py.File(io.BufferedReader(f, buffer_size=65536), "r") as h:
        if "FP_Latitude" not in h:
            return []
        lat, lon = np.asarray(h["FP_Latitude"][:], float), np.asarray(h["FP_Longitude"][:], float)
    w, s, e, n = BOX
    keep = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n) & np.isfinite(lat) & np.isfinite(lon)
    return list(zip(lat[keep].tolist(), lon[keep].tolist()))


def day_cells(day, sat):
    """The cells hot on this date for one satellite, and how many granules were read."""
    bucket = BUCKETS[sat]
    files = [(k, sz) for k, sz in listing(bucket, day) if (noaafire.times_of(k) or (None,))[0]
             and noaafire.times_of(k)[0].hour in HOURS]
    files.sort()
    # coarse search: every third granule, then the neighbours of each hit (a pass is consecutive)
    checked, over = {}, set()
    with cf.ThreadPoolExecutor(8) as pool:
        idx = list(range(0, len(files), 3))
        for i, ok in zip(idx, pool.map(lambda i: touches(bucket, files[i][0]), idx)):
            checked[i] = ok
        frontier = [i for i, ok in checked.items() if ok]
        while frontier:
            nxt = sorted({j for i in frontier for j in (i - 2, i - 1, i + 1, i + 2)
                          if 0 <= j < len(files) and j not in checked})
            for j, ok in zip(nxt, pool.map(lambda j: touches(bucket, files[j][0]), nxt)):
                checked[j] = ok
            frontier = [j for j in nxt if checked[j]]
        over = sorted(i for i, ok in checked.items() if ok)
        cells = set()
        for px in pool.map(lambda i: pixels(bucket, *files[i]), over):
            for la, lo in px:
                cells.add((math.floor(la / CELL), math.floor(lo / CELL)))
    return cells, len(over), len(checked)


def read_date(day):
    """One sampled date, both satellites (run in its own process)."""
    t0 = time.time()
    hot, info = set(), {}
    for sat in ("n20", "n21"):
        try:
            cells, n_over, n_checked = day_cells(day, sat)
        except Exception as e:  # noqa: BLE001
            print(day, sat, "failed:", e, flush=True)
            cells, n_over, n_checked = set(), 0, 0
        hot |= cells
        info[sat] = [n_over, n_checked]
    return day, hot, info, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-03-05")
    ap.add_argument("--end", default=None)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "rasad" / "data" / "heat_atlas.json.gz"))
    ap.add_argument("--progress", default=None, help="a JSON file to resume from and save to")
    ap.add_argument("--workers", type=int, default=8, help="dates read at the same time (processes)")
    a = ap.parse_args()
    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today() - dt.timedelta(days=2)
    dates = []
    d = start
    while d <= end:
        dates.append(d)
        d += dt.timedelta(days=a.step)
    prog = {"done": {}, "counts": {}}
    if a.progress and Path(a.progress).exists():
        prog = json.loads(Path(a.progress).read_text())
    counts = {k: v for k, v in prog["counts"].items()}
    todo = [d for d in dates if d.isoformat() not in prog["done"]]
    # processes, not threads: h5py lets one thread read at a time, and the reads wait on the network
    with cf.ProcessPoolExecutor(a.workers) as pool:
        for day, hot, info, took in pool.map(read_date, todo):
            if not any(v[0] for v in info.values()):
                print(day, "no granule over the box: date left out", flush=True)
                continue
            for i, j in hot:
                k = f"{i},{j}"
                counts[k] = counts.get(k, 0) + 1
            prog["done"][day.isoformat()] = info
            prog["counts"] = counts
            if a.progress:
                Path(a.progress).write_text(json.dumps(prog))
            print(day, "hot cells", len(hot), "granules", info, f"{took:.0f}s", f"({len(prog['done'])}/{len(dates)})", flush=True)
    kept = {k: v for k, v in counts.items() if v >= 2}
    atlas = {"cell": CELL, "box": BOX, "dates": len(prog["done"]), "first": min(prog["done"]), "last": max(prog["done"]),
             "step_days": a.step, "satellites": ["NOAA-20", "NOAA-21"], "source": "NOAA VIIRS EFIRE (AWS)",
             "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "cells": kept}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        json.dump(atlas, f, separators=(",", ":"))
    print("atlas:", len(kept), "cells on 2+ of", atlas["dates"], "dates ->", a.out, flush=True)


if __name__ == "__main__":
    main()
