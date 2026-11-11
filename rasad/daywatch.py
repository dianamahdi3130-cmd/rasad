"""Experimental daylight sea candidates from rendered Meteosat pictures.

The WMS channels contain display indices, not calibrated brightness temperatures
or fire radiative power. These conservative image tests identify places for
review; they cannot establish that a fire exists or measure its power.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .geodb import land_mask_points, ring_points
from .solar import solar_geometry
from .util import iso, px_to_latlon

SRC = "mtg_day_spot"


def _sea_ring(image, sea, row, col, km, inner=2.0, outer=15.0):
    """Sea display indices around a candidate, excluding its central footprint."""
    radius = int(math.ceil(outer / km))
    r0, c0 = max(0, row - radius), max(0, col - radius)
    win = image[r0:row + radius + 1, c0:col + radius + 1]
    yy, xx = np.ogrid[r0:r0 + win.shape[0], c0:c0 + win.shape[1]]
    distance = np.hypot(yy - row, xx - col) * km
    ring = (sea[r0:row + radius + 1, c0:col + radius + 1]
            & (distance >= inner) & (distance <= outer) & np.isfinite(win))
    return win[ring]


def find_spots(cfg, pics, box, t, mem=None, geo=None, refs=None):
    """Return review candidates and notes, or ``(None, reason)`` to retry a slot.

    Both reference pictures and all three validation channels are mandatory.
    Every threshold referring to a picture is in uncalibrated display indices.
    """
    # Import locally: seawatch dispatches daylight slots to this module.
    from .seawatch import (above_clear_sea, box_size, components, local_mean,
                           robust_sigma, sea_grid, warm_extent_km)

    C = cfg["seawatch"]
    D = C.get("daytime", {})
    offsets = D.get("ref_minutes", [30, 60]) if refs is None else refs
    try:
        offsets = [int(m) for m in offsets]
    except (ValueError, TypeError):
        return None, "daytime references invalid"
    if len(set(offsets)) < 2 or any(m <= 0 for m in offsets):
        return None, "daytime requires two distinct reference pictures"
    km = float(C.get("km_per_px", 0.5))
    W, H = box_size(box, km)
    bbox = box["bbox"]
    w, s, e, n = bbox
    lat = n - (np.arange(H)[:, None] + 0.5) / H * (n - s)
    lon = w + (np.arange(W)[None, :] + 0.5) / W * (e - w)
    times = [t] + [t - dt.timedelta(minutes=m) for m in offsets]
    eligible = np.ones((H, W), dtype=bool)
    glint_now = None
    # The night finder stops when the sky begins to lighten (sun at -6 degrees); this one takes
    # over from there, so dawn and dusk are no longer left to neither. The picture under test must
    # be in that light; the two it is compared with may still be night pictures. Glint needs the
    # sun above the horizon: only then is its angle asked for.
    first_light = float(D.get("min_sun_elev", -6))
    for when in times:
        sun, view, glint = solar_geometry(lat, lon, when)
        sun_up = np.isfinite(sun) & (sun > 0)
        fair = (np.isfinite(sun) & np.isfinite(view) & (view >= float(D.get("min_view_elev", 10)))
                & (~sun_up | (np.isfinite(glint) & (glint >= float(D.get("min_glint_angle", 25))))))
        if when == t:
            fair &= sun >= first_light
            glint_now = np.broadcast_to(glint, (H, W))
        eligible &= fair
    if not eligible.any():
        return [], "daytime illumination or glint guard"

    # Full-box requests share Pictures' underlying RGBA cache. Never treat absent
    # IR validation as zero cloud change, or let a missing reference become newness.
    channels = []
    for layer, channel in ((C.get("layer", "mtg_fd:rgb_firetemperature"), 0),
                           (C.get("layer", "mtg_fd:rgb_firetemperature"), 2),
                           (C.get("check_layer", "mtg_fd:ir105_hrfi"), None)):
        frames = []
        for when in times:
            frame = pics.get(layer, bbox, W, H, when, channel)
            if frame is None:
                return None, "daytime channel picture not ready"
            frame = np.asarray(frame, dtype=np.float32)
            if frame.shape != (H, W) or not np.isfinite(frame).all():
                return None, "daytime channel incomplete or nonfinite"
            if (frame < 0).any() or (frame > 255).any():
                return None, "daytime channel outside display-index range"
            frames.append(frame)
        channels.append(frames)
    red, blue, ir = channels
    sea = sea_grid(bbox, W, H, float(C.get("min_coast_km", 3)), km)
    if sea is None:
        return None, "daytime land mask unavailable"
    sea = np.asarray(sea, dtype=bool)
    if sea.shape != (H, W):
        return None, "daytime land mask incomplete"
    valid = sea & eligible
    if np.count_nonzero(valid) < 50:
        return [], "daytime no eligible open sea"

    reference = np.maximum.reduce(red[1:])
    rise = red[0] - reference
    radius = max(2, round(5 / km))
    spot = rise - local_mean(rise, radius)
    was_hot = reference - local_mean(reference, max(4, round(12 / km)))
    sigma = robust_sigma(np.where(valid, spot, np.nan))
    if sigma is None:
        return [], "daytime insufficient background"
    minimum = float(D.get("min_rise", 12))
    zmin = float(D.get("min_z", 8))
    candidate = valid & (rise >= minimum) & (spot >= max(minimum, zmin * sigma))
    notes = {"mode": "daytime", "experimental": True, "units": "display indices",
             "sigma": round(sigma, 2), "references_min": offsets, "candidates": 0,
             "validated": 0, "rejected": {}}

    def reject(reason, number=1):
        notes["rejected"][reason] = notes["rejected"].get(reason, 0) + number

    # One anomalous cloud field must not turn into thousands of expensive checks.
    if np.count_nonzero(candidate) > 5000:
        reject("too many anomalous pixels")
        return [], notes
    blobs = components(candidate, limit=5000)
    notes["candidates"] = len(blobs)
    ranked = []
    for points in blobs:
        peak = int(np.argmax(spot[points[:, 0], points[:, 1]]))
        row, col = map(int, points[peak])
        ranked.append((float(spot[row, col]), row, col, len(points)))
    ranked.sort(reverse=True)
    # On a morning of small sunlit clouds there are dozens of bright specks; the checks below are
    # cheap arithmetic, so enough of them are looked at that a fire is not crowded out by clouds.
    budget = max(0, int(D.get("max_candidates", 40)))
    if len(ranked) > budget:
        reject("validation budget", len(ranked) - budget)
    if not ranked or budget == 0:
        return [], notes

    blue_rise = blue[0] - np.maximum.reduce(blue[1:])
    blue_contrast = blue[0] - local_mean(blue[0], radius)
    ir_change = ir[0] - np.mean(ir[1:], axis=0)
    ir_local = ir_change - local_mean(ir_change, radius)
    ir_sigma = max(robust_sigma(np.where(valid, ir_local, np.nan)) or 0.5, 0.5)
    mask_fn = land_mask_points()
    if mask_fn is None:
        return None, "daytime land mask unavailable"
    out = []
    max_area = float(D.get("max_area_km2", 40)) / (km * km)
    for strength, row, col, area in ranked[:budget]:
        notes["validated"] += 1
        if area > max_area:
            reject("too big (clouds)")
            continue
        if was_hot[row, col] > float(D.get("max_was_hot", 8)):
            reject("hot before (flare)")
            continue
        above = above_clear_sea(red[0], valid, row, col, km)
        if above is None:
            return None, "daytime clear-sea background incomplete"
        if above < float(D.get("min_above_sea", 12)):
            reject("no warmer than clear sea")
            continue
        extent = warm_extent_km(rise, row, col, km)
        if extent > float(D.get("max_warm_extent_km", 8)):
            reject("part of a long warming band")
            continue
        center = (slice(max(0, row - 1), row + 2), slice(max(0, col - 1), col + 2))
        if (np.max(blue_contrast[center]) > float(D.get("max_blue_contrast", 12))
                or np.max(blue_rise[center]) > float(D.get("max_blue_rise", 10))):
            reject("blue-channel reflection or cloud")
            continue
        ir_sea = _sea_ring(ir[0], valid, row, col, km)
        if ir_sea.size < 30:
            return None, "daytime IR clear-sea background incomplete"
        # IR105's rendered warm end is dark, hence the LOWER sea percentile.
        cold_excess = float(np.max(ir[0][center]) - np.percentile(ir_sea, 10))
        if cold_excess > float(D.get("max_ir_cold_excess", 20)):
            reject("cold cloud in IR105")
            continue
        z10 = float(np.max(np.abs(ir_local[center]))) / ir_sigma
        if z10 >= float(D.get("max_ir_change_z", 3)):
            reject("cloud change (IR105)")
            continue
        latitude, longitude = px_to_latlon(bbox, W, H, col, row)
        coast = float(C.get("min_coast_km", 3))
        la, lo = ring_points(latitude, longitude, (0, coast / 2, coast))
        if np.asarray(mask_fn(la, lo)).any():
            reject("land or shore")
            continue
        if geo is not None and geo.platform_within(latitude, longitude, float(C.get("platform_km", 3))):
            reject("oil platform")
            continue
        reach = max(1, int(round(float(C.get("known_heat_km", 10)) / 2.2)))
        if mem is not None and mem.profile(latitude, longitude, t.date().isoformat(), 30, reach)[0] > 0:
            reject("heat seen here before")
            continue
        out.append({"src": SRC, "family": "mtg", "kind": "geo", "t": iso(t),
                    "lat": round(latitude, 4), "lon": round(longitude, 4), "frp": None,
                    "daytime": True, "experimental": True, "rise": round(strength, 1),
                    "z": round(strength / sigma, 1), "z10": round(z10, 1), "area_px": area,
                    "above_sea": round(above, 1), "extent_km": round(extent, 1),
                    # no glint angle before sunrise (never NaN: the app could not read the data)
                    "glint_angle": round(float(glint_now[row, col]), 1) if np.isfinite(glint_now[row, col]) else None})
    burst = max(0, int(D.get("burst_max", 3)))
    if len(out) > burst:
        # Many simultaneous specks are usually weather. Retain only exceptionally
        # strong candidates, still capped at the configured review limit.
        strong = [sp for sp in out if sp["z"] >= 2 * zmin and sp["rise"] >= 2 * minimum][:burst]
        reject("weather (many specks at once)", len(out) - len(strong))
        out = strong
    return out, notes
