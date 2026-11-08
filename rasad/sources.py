"""Where fire detections come from: European geostationary fire products and NASA FIRMS."""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from .util import UTC, bbox_intersects, bbox_px, iso, log, px_to_latlon
from .wms import components


def union_bbox(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def region_tiles(regions, max_deg=8.0):
    """Grid of tiles (<= max_deg) covering every enabled region."""
    boxes = [r["bbox"] for r in regions]
    w, s, e, n = union_bbox(boxes)
    nx, ny = max(1, math.ceil((e - w) / max_deg)), max(1, math.ceil((n - s) / max_deg))
    dx, dy = (e - w) / nx, (n - s) / ny
    tiles = []
    for i in range(nx):
        for j in range(ny):
            t = [w + i * dx, s + j * dy, w + (i + 1) * dx, s + (j + 1) * dy]
            if any(bbox_intersects(t, b) for b in boxes):
                tiles.append([round(v, 4) for v in t])
    return tiles


def pick_frp(props):
    for k, v in (props or {}).items():
        if "frp" in str(k).lower():
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def detect_geo_layer(wms, L, tile, t):
    """Fire symbols drawn by a WMS fire layer over one tile -> detections (None on error)."""
    W, H = bbox_px(tile, int(L.get("tile_px", 1600)))
    img, actual = wms.getmap(L["name"], tile, W, H, t=t, style=L.get("style", ""), quiet=True)
    if img is None:
        return None
    # WMS can substitute a nearby picture when the requested product is not ready.
    # Leave this slot unread so the engine retries it when its own picture arrives.
    if actual is not None and abs((actual - t).total_seconds()) > 120:
        return None
    arr = np.asarray(img)
    mask = arr[..., 3] > 40
    if mask.mean() > 0.25:
        log.warning("%s: %.0f%% of the image is opaque; not a fire-symbol layer, skipped.",
                    L["name"], mask.mean() * 100)
        return None
    when = actual or t
    out = []
    for pts in components(mask):
        rows, cols = pts[:, 0], pts[:, 1]
        r, c = float(rows.mean()), float(cols.mean())
        lat, lon = px_to_latlon(tile, W, H, c, r)
        out.append({"src": L["id"], "family": L["family"], "kind": "geo", "t": iso(when),
                    "lat": round(lat, 4), "lon": round(lon, 4), "frp": None,
                    # where to ask the server for this fire's strength, only if it matters
                    "_fi": [L["name"], tile, W, H, c, r, iso(when)] if L.get("feature_info", True) else None})
    return out


def fetch_frp(wms, det):
    """Ask the WMS server for the fire strength of one symbol (lazy, used for incidents only)."""
    fi = det.pop("_fi", None)
    if not fi or det.get("frp") is not None:
        return
    from .util import parse_time
    layer, tile, W, H, c, r, t = fi
    det["frp"] = pick_frp(wms.feature_info(layer, tile, W, H, c, r, t=parse_time(t)))


FIRMS_FAMILY = {"VIIRS": "viirs", "MODIS": "modis", "LANDSAT": "landsat"}


def firms_fetch(session, cfg, bbox, days, date=None, *, status=None):
    """Read FIRMS in bounded two-request waves, preserving deterministic source order.

    ``status`` distinguishes valid empty CSVs from missing responses. A shared
    deadline stops new requests; each in-flight request uses the remaining time.
    The surrounding monitor also bounds the complete collection process.
    """
    F = cfg["firms"]
    key = str(F.get("map_key") or "").strip()
    summary = {"attempted": 0, "completed": 0, "failed": 0, "deferred": 0, "ok": False}
    if status is not None:
        status.update(summary)
    if not key:
        return []
    sources = list(F.get("sources") or [])
    if sources:
        offset = int(F.get("source_offset", 0)) % len(sources)
        sources = sources[offset:] + sources[:offset]
    budget = max(0.0, float(F.get("budget_s", 25)))
    request_limit = max(0.1, float(F.get("request_timeout_s", 20)))
    workers = max(1, min(2, int(F.get("max_workers", 2))))
    deadline = time.monotonic() + budget
    w, s, e, n = bbox

    def fetch(src):
        timeout = min(request_limit, deadline - time.monotonic())
        if timeout <= 0:
            return [], False, False
        url = (f"{F['base_url'].rstrip('/')}/api/area/csv/{key}/{src}/"
               f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}/{int(days)}" + (f"/{date}" if date else ""))
        try:
            r = session.get(url, timeout=timeout)
        except requests.RequestException as ex:
            # Requests errors can include the whole URL, whose path contains the API key.
            log.warning("FIRMS %s: request failed (%s)", src, type(ex).__name__)
            return [], True, False
        text = r.text.strip().lstrip("\ufeff")
        if r.status_code != 200:
            log.warning("FIRMS %s: HTTP %s", src, r.status_code)
            return [], True, False
        reader = csv.DictReader(io.StringIO(text))
        if not {"latitude", "longitude", "acq_date", "acq_time"} <= set(reader.fieldnames or []):
            log.warning("FIRMS %s: no valid hotspot CSV", src)
            return [], True, False
        family = next((v for k, v in FIRMS_FAMILY.items() if src.startswith(k)), "polar")
        rows = []
        try:
            for row in reader:
                try:
                    lat, lon = float(row["latitude"]), float(row["longitude"])
                    if not (math.isfinite(lat) and math.isfinite(lon)
                            and -90 <= lat <= 90 and -180 <= lon <= 180):
                        continue
                    hhmm = str(row.get("acq_time", "0")).strip().zfill(4)
                    t = dt.datetime.strptime(row["acq_date"] + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=UTC)
                except (KeyError, TypeError, ValueError):
                    continue
                try:
                    frp = float(row.get("frp") or "nan")
                except (TypeError, ValueError):
                    frp = float("nan")
                detection = {"src": src, "family": family, "kind": "polar", "t": iso(t),
                             "lat": round(lat, 4), "lon": round(lon, 4),
                             "frp": frp if math.isfinite(frp) and frp >= 0 else None,
                             "conf": str(row.get("confidence", "")).strip().lower(),
                             "night": str(row.get("daynight", "")).strip().upper() == "N"}
                for dimension in ("scan", "track"):
                    try:
                        value = float(row.get(dimension) or "nan")
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(value) and value > 0:
                        detection[dimension] = value
                for field in ("instrument", "satellite"):
                    value = str(row.get(field) or "").strip()
                    if value:
                        detection[field] = value[:80]
                rows.append(detection)
        except csv.Error:
            log.warning("FIRMS %s: unreadable hotspot CSV", src)
            return [], True, False
        return rows, True, True

    out = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for first in range(0, len(sources), workers):
            if time.monotonic() >= deadline:
                summary["deferred"] += len(sources) - first
                break
            results = list(pool.map(fetch, sources[first:first + workers]))
            for rows, attempted, completed in results:
                summary["attempted"] += int(attempted)
                summary["completed"] += int(completed)
                summary["failed"] += int(attempted and not completed)
                summary["deferred"] += int(not attempted)
                out.extend(rows)
    summary["ok"] = bool(summary["completed"] and not summary["failed"] and not summary["deferred"])
    if status is not None:
        status.update(summary)
    return out
