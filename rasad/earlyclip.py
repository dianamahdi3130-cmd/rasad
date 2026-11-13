"""Evidence loops for a first accepted night spot, using its original detector grid.

The image service can render a different-looking signal for a narrower request.
Download the recorded detector raster, then crop locally. No future picture,
second accepted spot, inferred temperature or invented fire power is required.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .clip import NotVisible, _erode, _write
from .seawatch import Pictures, box_size
from .twopanel import BG, CYAN, DIM, FIRE, WHITE, _font, _lut, _text, _width
from .util import in_bbox, iso, latlon_to_px, parse_time

STYLE = 2
NEAREST = getattr(Image, "Resampling", Image).NEAREST


def _grid(cfg, inc):
    """Resolve and validate the actual raster, preserving recorded provenance."""
    C = cfg["seawatch"]
    grid = inc.get("source_grid")
    if grid is not None:
        if not isinstance(grid, dict):
            raise ValueError("invalid recorded source grid")
        try:
            bbox = list(grid["bbox"])
            width, height = grid["width"], grid["height"]
            km = grid["km_per_px"]
            layer = grid["layer"]
            numbers = bbox + [width, height, km]
            if (len(bbox) != 4 or any(isinstance(v, bool) or not math.isfinite(float(v)) for v in numbers)
                    or int(width) != width or int(height) != height):
                raise ValueError("invalid source geometry")
            bbox = list(map(float, bbox))
            width, height, km = int(width), int(height), float(km)
        except (KeyError, TypeError, OverflowError) as e:
            raise ValueError("invalid recorded source grid") from e
        if (not -180 <= bbox[0] < bbox[2] <= 180 or not -90 <= bbox[1] < bbox[3] <= 90
                or not 16 <= width <= 4096 or not 16 <= height <= 4096 or not 0 < km <= 10
                or (width, height) != box_size({"bbox": bbox}, km)
                or layer != C["layer"]):
            raise ValueError("recorded source grid differs from the detector geometry")
    else:
        # Legacy candidates did not store provenance. The first configured matching
        # finder box follows detector order; new candidates always carry the grid.
        matches = [b for b in C.get("boxes", []) if b.get("enabled", True)
                   and in_bbox(b["bbox"], inc["lat"], inc["lon"])]
        if not matches:
            raise ValueError("no enabled detector grid contains this spot")
        bbox = list(matches[0]["bbox"])
        km, layer = float(C["km_per_px"]), C["layer"]
        width, height = box_size(matches[0], km)
    if not in_bbox(bbox, inc["lat"], inc["lon"]):
        raise ValueError("spot is outside its recorded detector grid")
    return bbox, width, height, km, layer


def _read(pics, layer, bbox, width, height, when):
    # Pictures.get validates acquisition warnings and shares the exact full-grid
    # cache written by the scanner. Its public view is a channel, while the cache
    # also retains the same validated RGBA pixels for the original-image panel.
    if pics.get(layer, bbox, width, height, when, channel=0) is None:
        return None
    raw = pics.cache.get((layer, tuple(bbox), width, height, iso(when), "rgba"))
    return raw if Pictures._complete(raw, width, height) else None


def _label(d, xy, text, size, fill=WHITE, *, rtl=True, anchor="ma"):
    _text(d, xy, text, _font(size, "arb" if rtl else "mono"), fill, anchor=anchor, rtl=rtl)


def _frame(raw, when, crop, position, cfg, inc, km, coast=None):
    """Fixed display scale for every frame; no data-derived frame normalization."""
    width, height = 720, 480
    panel_w, panel_h = 336, 302
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    _label(draw, (width / 2, 10), "رصد حراري أولي، يحتاج تأكيداً", 23)
    title = str(inc.get("where") or "في البحر")
    original = title
    while title and _width(draw, title, _font(18), rtl=True) > 650:
        title = title[:-1]
    _label(draw, (width / 2, 46), title + ("…" if title != original else ""), 18, DIM)
    for x, text in ((184, "عرض حراري"), (536, "صورة القمر الأصلية")):
        _label(draw, (x, 77), text, 16, CYAN)
    left, top, right, bottom = crop
    rgb = raw[top:bottom, left:right, :3]
    # WMS RGB levels are an uncalibrated display index. 0..160 is held fixed
    # across the complete loop; neither this palette nor raw RGB represents MW.
    levels = np.clip(np.round(rgb[..., 0].astype(np.float32) / 160 * 255), 0, 255).astype(np.uint8)
    thermal = _lut(FIRE)[levels]
    if coast is not None:
        thermal[coast] = (180, 190, 198)
    center_x, center_y = position
    for x0, pixels in ((16, thermal), (368, rgb)):
        source = Image.fromarray(pixels)
        scale = min(panel_w / source.width, panel_h / source.height)
        target = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
        panel = source.resize(target, NEAREST)
        px, py = x0 + (panel_w - target[0]) // 2, 102 + (panel_h - target[1]) // 2
        image.paste(panel, (px, py))
        draw.rectangle((x0, 102, x0 + panel_w - 1, 102 + panel_h - 1), outline=CYAN, width=1)
        cx, cy = px + (center_x - left + .5) * scale, py + (center_y - top + .5) * scale
        radius = max(12, 3 / km * scale)
        # Radius shows approximate 3 km location uncertainty, not a fire's size.
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=CYAN, width=1)
        draw.line((cx - 5, cy, cx + 5, cy), fill=CYAN, width=1)
        draw.line((cx, cy - 5, cx, cy + 5), fill=CYAN, width=1)
    local = when + dt.timedelta(hours=float(cfg.get("local_time", {}).get("offset_hours", 3)))
    name = cfg.get("local_time", {}).get("name", "بغداد")
    _label(draw, (450, 410), "وقت الصورة المطلوب", 14, DIM)
    _label(draw, (320, 413), f"{local:%H:%M}", 15, rtl=False)
    _label(draw, (255, 410), name, 14)
    _label(draw, (16, 443), f"{when:%Y-%m-%d %H:%M} UTC", 14, DIM, rtl=False, anchor="la")
    _label(draw, (704, 443), f"{inc['lat']:.4f}N  {inc['lon']:.4f}E", 14, DIM,
           rtl=False, anchor="ra")
    _label(draw, (width / 2, 465), "© EUMETSAT · MTG", 11, DIM, rtl=False, anchor="mm")
    return image


def render(cfg, wms, inc, now):
    """Return an MP4/GIF and snapshot from a first spot plus its earlier reference.

    Only complete, different, correctly timed source rasters are evidence. A
    missing/weak current image fails cleanly; a later/other satellite picture
    cannot silently replace this particular observation.
    """
    if not inc.get("preliminary"):
        raise ValueError("first-spot renderer requires a preliminary observation")
    when = parse_time(inc.get("evidence_time") or inc["first"])
    if when > now:
        raise ValueError("cannot render a future observation")
    bbox, width, height, km, layer = _grid(cfg, inc)
    refs = inc.get("reference_minutes") or cfg["seawatch"].get("ref_minutes", [30])
    try:
        minutes = sorted({int(m) for m in refs if not isinstance(m, bool) and 0 < float(m) <= 180}, reverse=True)[:3]
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError("invalid reference minutes") from e
    if not minutes:
        raise ValueError("no earlier reference for the first-spot video")
    folder = cfg.get("_state_dir")
    cache = Path(folder).resolve().parent / ".rasad-pictures" if folder else None
    pics = Pictures(wms, cache_dir=cache)
    current = _read(pics, layer, bbox, width, height, when)
    if current is None:
        raise NotVisible("the actual first-spot raster is not available")
    shots = []
    for minute in minutes:
        stamp = when - dt.timedelta(minutes=minute)
        raw = _read(pics, layer, bbox, width, height, stamp)
        if raw is not None and not np.array_equal(raw, current):
            if not shots or not np.array_equal(raw, shots[-1][1]):
                shots.append((stamp, raw))
    if not shots:
        raise NotVisible("no different valid before image for this first spot")
    x, y = latlon_to_px(bbox, width, height, inc["lat"], inc["lon"])
    yy, xx = np.mgrid[:height, :width]
    distance = np.hypot(xx - x, yy - y) * km
    near = distance <= 3
    ring = (distance >= 4) & (distance <= 12)
    red = current[..., 0].astype(np.float32)
    before = np.maximum.reduce([raw[..., 0].astype(np.float32) for _, raw in shots])
    if (not near.any() or float(np.max((red - before)[near])) < float(cfg["seawatch"].get("min_rise", 6))
            or (ring.any() and float(np.max(red[near]) - np.median(red[ring]))
                < float(cfg["seawatch"].get("min_above_sea", 3)))):
        raise NotVisible("the first accepted spot does not show on its original raster")
    shots.append((when, current))
    radius = max(8, int(round(float(cfg["clip"].get("half_size_km", 20)) / km)))
    cx, cy = round(x), round(y)
    crop = (max(0, cx - radius), max(0, cy - radius), min(width, cx + radius + 1), min(height, cy + radius + 1))
    revision = {"style": STYLE, "id": inc["id"], "grid": [layer, bbox, width, height, km],
                "times": [iso(t) for t, _ in shots], "crop": crop, "where": inc.get("where"),
                "local_time": cfg.get("local_time"),
                "pixels": [hashlib.sha256(raw[crop[1]:crop[3], crop[0]:crop[2]].tobytes()).hexdigest()
                           for _, raw in shots]}
    digest = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()[:14]
    key = hashlib.sha256(str(inc["id"]).encode()).hexdigest()[:12]
    base = Path(cfg["_site_dir"]) / "clips" / f"early-{key}-{digest}"
    base.parent.mkdir(parents=True, exist_ok=True)
    coast = None
    try:
        from .geodb import land_mask_points
        mask = land_mask_points()
        if mask is not None:
            w, south, east, north = bbox
            lons = w + (np.arange(crop[0], crop[2]) + .5) / width * (east - w)
            lats = north - (np.arange(crop[1], crop[3]) + .5) / height * (north - south)
            gl, go = np.meshgrid(lats, lons, indexing="ij")
            land = mask(gl.ravel(), go.ravel()).reshape(gl.shape).astype(bool)
            coast = land & ~_erode(land)
    except Exception:  # a context outline must not prevent an evidence video
        coast = None
    frames = [_frame(raw, t, crop, (x, y), cfg, inc, km, coast) for t, raw in shots]
    snapshot = Path(f"{base}.jpg")
    frames[-1].save(snapshot, quality=92)
    # One second per genuine image and a two-second hold at detection. Holds
    # repeat the same frame rather than inventing intermediate observations.
    repeated = [frame for frame in frames for _ in range(2)] + [frames[-1]] * 4
    return _write(repeated, base, 2, str(cfg["clip"].get("format", "mp4"))), snapshot
