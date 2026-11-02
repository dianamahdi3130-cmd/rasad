"""Where fire detections come from: European geostationary fire products and NASA FIRMS."""
from __future__ import annotations

import csv
import datetime as dt
import io
import math

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
    img, actual = wms.getmap(L["name"], tile, W, H, t=t, style=L.get("style", ""))
    if img is None:
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
                    "_fi": [L["name"], tile, W, H, c, r, iso(t)] if L.get("feature_info", True) else None})
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


def firms_fetch(session, cfg, bbox, days, date=None):
    """Hotspots from NASA FIRMS (VIIRS 375 m / MODIS 1 km) for a box."""
    F = cfg["firms"]
    key = str(F.get("map_key") or "").strip()
    if not key:
        return []
    out = []
    w, s, e, n = bbox
    for src in F["sources"]:
        url = (f"{F['base_url'].rstrip('/')}/api/area/csv/{key}/{src}/"
               f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}/{int(days)}" + (f"/{date}" if date else ""))
        try:
            r = session.get(url, timeout=120)
        except requests.RequestException as ex:
            log.warning("FIRMS %s: %s", src, ex)
            continue
        text = r.text.strip()
        if r.status_code != 200 or not text.lower().startswith("latitude"):
            log.warning("FIRMS %s: %s", src, text[:200] or f"HTTP {r.status_code}")
            continue
        family = next((v for k, v in FIRMS_FAMILY.items() if src.startswith(k)), "polar")
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
                hhmm = str(row.get("acq_time", "0")).strip().zfill(4)
                t = dt.datetime.strptime(row["acq_date"] + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=UTC)
                frp = float(row.get("frp") or "nan")
            except (KeyError, ValueError):
                continue
            out.append({"src": src, "family": family, "kind": "polar", "t": iso(t),
                        "lat": round(lat, 4), "lon": round(lon, 4),
                        "frp": None if math.isnan(frp) else frp,
                        "conf": str(row.get("confidence", "")).strip().lower(),
                        "night": str(row.get("daynight", "")).strip().upper() == "N"})
    return out
