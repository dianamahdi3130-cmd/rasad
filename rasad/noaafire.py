"""NASA-family fire pixels read from NOAA's own VIIRS files, about 25 minutes after a day pass and 45
after a night pass, instead of about three hours through FIRMS.

NOAA-20 and NOAA-21 are the same satellites FIRMS reports as VIIRS_NOAA20_NRT / VIIRS_NOAA21_NRT, and
NOAA publishes their active-fire files (EFIRE v1r3, one per 84-second granule) in public AWS buckets,
no account needed. Measured on 14-15 Nov 2026 over Rasad's regions: 99% of NOAA's pixels are FIRMS
rows (median 60 m apart, 99% within 350 m), the confidence classes agreed in 400 of 400 sightings,
the strength within 10% in 92%; the files were out 16-30 minutes after a day pass and 39-49 minutes
after a night pass (docs/reviews/noaa_files_20261116.md).

So these pixels enter the check as FIRMS rows of the same source and satellite. When FIRMS later
brings the same pixel, the check takes it as the twin of the one already counted (`twins`), never as
a second sighting.

Each file's area is read from its first 32 KB (the "geospatial_bounds" polygon); only files that touch
Rasad's box are downloaded (about 30 per satellite a day, 40 MB). A cursor per satellite remembers the
last file handled, so every check reads only what is new. Every request has a time budget."""
from __future__ import annotations

import datetime as dt
import io
import math
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from .util import haversine_km, iso, log, parse_time

SATELLITES = {
    "n20": {"bucket": "noaa-nesdis-n20-pds", "src": "VIIRS_NOAA20_NRT", "satellite": "N20"},
    "n21": {"bucket": "noaa-nesdis-n21-pds", "src": "VIIRS_NOAA21_NRT", "satellite": "N21"},
}
PRODUCT = "VIIRS_EFIRE_VIIRSI_EDR"
CONFIDENCE = {7: "l", 8: "n", 9: "h"}       # FP_FireMask: low, nominal, high (as FIRMS)
NADIR_AREA_KM2 = 0.554                      # FP_PixelArea at nadir, where FIRMS says scan = track = 0.48 km
NADIR_SIZE_KM = 0.48
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_NAME = re.compile(r"_s(\d{14})(\d)_e(\d{14})(\d)_c")
_POLYGON = re.compile(rb"POLYGON\(\(([^)]*)\)\)")


def times_of(key):
    """Start and end of a granule from its file name (to the tenth of a second)."""
    m = _NAME.search(key)
    if not m:
        return None
    def t(digits, tenth):
        return dt.datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc) + \
            dt.timedelta(seconds=int(tenth) / 10)
    return t(m.group(1), m.group(2)), t(m.group(3), m.group(4))


def key_at(sat, when):
    """A name that sorts just before the granules starting at `when` (a cursor to start after)."""
    return (f"{PRODUCT}/{when:%Y/%m/%d}/EFIRE-VIIRSI_v1r3_{sat}_s{when:%Y%m%d%H%M%S}0")


def bounds_touch(polygon, box):
    """Does a granule's outline (lon, lat points) reach the box [w, s, e, n]?"""
    lons = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    if not lons or max(lons) - min(lons) > 180:     # crosses the date line: far from the Gulf
        return False
    w, s, e, n = box
    return not (max(lons) < w or min(lons) > e or max(lats) < s or min(lats) > n)


def outline(head):
    """The granule's outline from the first bytes of its file, or None."""
    m = _POLYGON.search(head or b"")
    if not m:
        return None
    try:
        return [tuple(float(v) for v in p.split()) for p in m.group(1).decode().split(",")]
    except ValueError:
        return None


def rows_from(data, key, sat, box):
    """The fire pixels of one file inside the box, shaped like FIRMS rows."""
    import h5py
    import numpy as np
    start, end = times_of(key)
    span = (end - start).total_seconds()
    info = SATELLITES[sat]
    with h5py.File(io.BytesIO(data), "r") as f:
        if "FP_Latitude" not in f:
            return []
        lat, lon = np.asarray(f["FP_Latitude"][:], float), np.asarray(f["FP_Longitude"][:], float)
        frp = np.asarray(f["FP_FireRadiativePower"][:], float)
        mask = np.asarray(f["FP_FireMask"][:], int)
        day = np.asarray(f["FP_Day"][:], int)
        line = np.asarray(f["FP_Line"][:], float)
        area = np.asarray(f["FP_PixelArea"][:], float)
        lines = max(1, int(f["y"].shape[0]) if "y" in f else 1536)
    w, s, e, n = box
    out = []
    for i in range(len(lat)):
        la, lo = float(lat[i]), float(lon[i])
        if not (math.isfinite(la) and math.isfinite(lo) and w <= lo <= e and s <= la <= n):
            continue
        # the time of the pixel's own scan, to the minute as FIRMS gives it
        t = start + dt.timedelta(seconds=span * min(max(line[i], 0.0) + 0.5, lines) / lines)
        t = t.replace(second=0, microsecond=0)
        size = NADIR_SIZE_KM * math.sqrt(area[i] / NADIR_AREA_KM2) if math.isfinite(area[i]) and area[i] > 0 \
            else NADIR_SIZE_KM
        value = float(frp[i])
        out.append({"src": info["src"], "family": "viirs", "kind": "polar", "t": iso(t),
                    "lat": round(la, 4), "lon": round(lo, 4),
                    "frp": round(value, 2) if math.isfinite(value) and value >= 0 else None,
                    "conf": CONFIDENCE.get(int(mask[i]), ""), "night": int(day[i]) == 0,
                    "scan": round(size, 2), "track": round(size, 2),
                    "instrument": "VIIRS", "satellite": info["satellite"], "via": "noaa"})
    return out


def _listing(session, bucket, prefix, after, timeout):
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        elif after:
            params["start-after"] = after
        r = session.get(f"https://{bucket}.s3.amazonaws.com/", params=params, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        keys += [c.find(_NS + "Key").text for c in root.findall(_NS + "Contents")]
        nxt = root.find(_NS + "NextContinuationToken")
        if nxt is None:
            return keys
        token = nxt.text


def collect(cfg, session, state, now, box, *, status=None):
    """New NOAA fire pixels inside `box` since the last check. Never waits past the budget; what is
    left is read in the next check (the cursor only moves past files fully handled)."""
    C = cfg.get("noaa") or {}
    summary = {"read": 0, "over": 0, "pixels": 0, "failed": 0, "ok": False, "latest": None}
    if status is not None:
        status.update(summary)
    if not C.get("enabled", True):
        return []
    deadline = time.monotonic() + float(C.get("budget_s", 12))
    limit = float(C.get("request_timeout_s", 10))
    store = state.setdefault("noaa", {})
    oldest = now - dt.timedelta(hours=float(C.get("max_age_hours", 6)))
    rows, ok = [], True

    def left():
        return deadline - time.monotonic()

    def get(url, headers=None):
        timeout = min(limit, left())
        if timeout < 1:
            raise TimeoutError("no time left")
        r = session.get(url, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.content

    for sat, info in SATELLITES.items():
        bucket = info["bucket"]
        after = store.get(sat) or key_at(sat, now - dt.timedelta(hours=float(C.get("first_hours", 3))))
        after = max(after, key_at(sat, oldest))
        try:
            keys = []
            day = dt.datetime.strptime(after.rsplit("_s", 1)[-1][:8], "%Y%m%d").replace(tzinfo=dt.timezone.utc)
            while day.date() <= now.date():
                prefix = f"{PRODUCT}/{day:%Y/%m/%d}/"
                keys += _listing(session, bucket, prefix, after if after.startswith(prefix) else None,
                                 min(limit, max(1.0, left())))
                day += dt.timedelta(days=1)
        except (requests.RequestException, ET.ParseError, ValueError, TimeoutError) as e:
            log.warning("NOAA %s: listing failed (%s)", sat, type(e).__name__)
            summary["failed"] += 1
            ok = False
            continue
        keys = [k for k in keys if k > after and times_of(k)]
        workers = max(1, int(C.get("workers", 4)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for first in range(0, len(keys), workers):
                if left() < 2:
                    ok = False
                    break
                batch = keys[first:first + workers]
                heads = list(pool.map(lambda k: _try(get, f"https://{bucket}.s3.amazonaws.com/{k}",
                                                     {"Range": "bytes=0-32767"}), batch))
                stop = False
                for k, head in zip(batch, heads):
                    if head is None:
                        summary["failed"] += 1
                        stop = True
                        break
                    summary["read"] += 1
                    poly = outline(head)
                    if poly is not None and bounds_touch(poly, box):
                        data = _try(get, f"https://{bucket}.s3.amazonaws.com/{k}")
                        if data is None:
                            summary["failed"] += 1
                            stop = True
                            break
                        try:
                            found = rows_from(data, k, sat, box)
                        except (OSError, KeyError, ValueError) as e:
                            log.warning("NOAA %s: unreadable file %s (%s)", sat, k.rsplit("/", 1)[-1], type(e).__name__)
                            found = []
                        summary["over"] += 1
                        rows += found
                    store[sat] = k             # handled: never read again
                if stop:
                    ok = False
                    break
    summary["pixels"] = len(rows)
    summary["latest"] = max((r["t"] for r in rows), default=None)
    summary["ok"] = ok
    if status is not None:
        status.update(summary)
    return rows


def _try(get, url, headers=None):
    try:
        return get(url, headers)
    except (requests.RequestException, TimeoutError):
        return None


class Twins:
    """A pixel reaches Rasad twice: from NOAA's file first, from FIRMS hours later. The second one is
    the same sighting. Matched one to one, same source, at most 4 minutes and 0.7 km apart (measured:
    99% of twins within 0.35 km, all within 0.63 km; FIRMS's minute and the file's scan minute differ
    by at most 2.6 minutes). `seen` holds the FIRMS rows already counted (src|t|lat|lon), `noaa_keys`
    the NOAA pixels of the last day (key -> 0, or 1 once its FIRMS twin has come)."""

    def __init__(self, seen, noaa_keys, max_km=0.7, max_s=240):
        self.max_km, self.max_s = max_km, max_s
        self.noaa_keys = noaa_keys
        self.by_src = {}
        for k in list(seen) + list(noaa_keys):
            self._index(k, k in noaa_keys)

    def _index(self, key, noaa, det=None):
        try:
            if det is None:
                src, t, lat, lon = key.split("|")
                lat, lon = float(lat), float(lon)
            else:
                src, t, lat, lon = det["src"], det["t"], float(det["lat"]), float(det["lon"])
            if not src.startswith("VIIRS_NOAA"):
                return
            self.by_src.setdefault(src, []).append((parse_time(t), lat, lon, key, noaa))
        except (KeyError, ValueError):
            pass

    def _near(self, det, want_noaa):
        t = parse_time(det["t"])
        best = None
        for when, lat, lon, k, noaa in self.by_src.get(det["src"], []):
            if noaa != want_noaa or abs((when - t).total_seconds()) > self.max_s:
                continue
            if want_noaa and self.noaa_keys.get(k):
                continue                           # this NOAA pixel already has its twin
            d = haversine_km(det["lat"], det["lon"], lat, lon)
            if d <= self.max_km and (best is None or d < best[0]):
                best = (d, k)
        return best[1] if best else None

    def absorbs(self, det):
        """True when `det` is the second copy of a pixel already counted (and marks the pair)."""
        if not str(det.get("src", "")).startswith("VIIRS_NOAA"):
            return False
        if det.get("via") == "noaa":
            return self._near(det, want_noaa=False) is not None   # FIRMS was first (NOAA files late)
        k = self._near(det, want_noaa=True)
        if k is None:
            return False
        self.noaa_keys[k] = 1
        return True

    def add(self, key, det, noaa=False):
        """Remember a pixel just counted, so a later copy in the same check finds it."""
        self._index(key, noaa, det)
