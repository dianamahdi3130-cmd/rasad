"""Rasad's own hot-spot finder on Meteosat-12's Fire Temperature picture, every 10 minutes.

The official fire product only flags fires above roughly 20-50 MW at the angle Meteosat-12 sees
the Gulf, so a small ship fire (the Hormuz fire of 1 November 2026 was about 5 MW) is not flagged,
although it does show in the picture as a new warm speck. This finder looks for exactly that,
at night and at sea, where the background is calm. Experimental daytime checks live in
daywatch.py, with separate solar-reflection filters and a requirement for independent confirmation.

The picture is asked for at full detail (0.25 km per pixel). Asked for at 0.5 km or coarser, the
server answers from a blurred copy that is also one picture (10 minutes) behind: a fire two pixels
wide all but disappears in it (docs/reviews/hormuz_ras_al_khaimah_20261114.md). The red colour
carries the 3.8 micron channel; green and blue carry 2.2 and 1.6 micron, which are dark at night
except over flames, so a glow there is evidence no cloud can fake.

A speck is kept only if:
  * it is warmer than the same spot was in the pictures 30, 60 and 90 minutes earlier (their
    middle value, so a fire that keeps burning keeps showing for an hour), by a clear margin, or
    by a smaller one together with a new glow in the short-wave colours;
  * it is small (a fire, not a sheet of clearing sky) and stands above the clear sea around it;
  * the sea around it is calm: where clouds drift, the pixels for kilometres around change too;
  * the spot was not already hot before (a flare that flickers), and it is not the glow around a
    much stronger source nearby (a big flare's halo brightens and fades with it);
  * it is at sea, away from the shore, from oil platforms and from heat seen there before;
  * the 10.5 micron picture did not change there the same way (a hole in a cloud changes
    both pictures; a small fire changes almost only the 3.8 micron one).
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import numpy as np
from PIL import Image, PngImagePlugin

from .geodb import land_mask_points, ring_points
from .thermal_history import history_rules, persistent_local_heat
from .util import box_around, floor_time, iso, log, parse_time, px_to_latlon, sun_elevation
from .wms import Availability, components

SRC = "mtg_spot"
DAY_SRC = "mtg_day_spot"
FINDER_SOURCES = frozenset({SRC, DAY_SRC})


def _win_sum(a, r):
    """Sum over a (2r+1) x (2r+1) window around every pixel (integral image)."""
    H, W = a.shape
    k = 2 * r + 1
    c = np.zeros((H + k, W + k), np.float64)
    c[1:, 1:] = np.pad(a, r, mode="constant").cumsum(0).cumsum(1)
    return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]


def local_mean(x, r):
    ok = ~np.isnan(x)
    return (_win_sum(np.where(ok, x, 0.0), r) / np.maximum(_win_sum(ok.astype(np.float64), r), 1.0)
            ).astype(np.float32)


def _gray(img, channel=None):
    if img is None:
        return None
    a = np.asarray(img)
    if a[..., 3].mean() < 30:
        return None  # nothing drawn: no data for that time
    g = (a[..., :3].astype(np.float32).mean(axis=2) if channel is None
         else a[..., channel].astype(np.float32))
    g[a[..., 3] < 128] = np.nan
    return g


def robust_sigma(x):
    v = x[~np.isnan(x)]
    if v.size < 50:
        return None
    med = np.median(v)
    return float(1.4826 * np.median(np.abs(v - med))) or 0.3


class Pictures:
    """Share pictures in memory and optionally across short monitoring cycles.

    The disk cache contains complete, time-validated rasters only. Missing or
    partially drawn pictures remain retryable, rather than becoming evidence.
    """

    CACHE_VERSION = 1
    CACHE_TTL_S = 6 * 60 * 60
    CACHE_MAX_BYTES = 256 * 1024 * 1024
    _CACHE_NAME = re.compile(r"^[0-9a-f]{64}\.png$")

    def __init__(self, wms, cache_dir=None, attempts=2):
        self.wms, self.cache = wms, {}
        self.attempts = max(1, int(attempts))
        self._network_allowed = True
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._prune_disk()
            except OSError as exc:
                log.debug("picture cache unavailable: %s", exc)
                self.cache_dir = None

    def _disk_key(self, base):
        layer, bbox, width, height, requested = base
        endpoint = getattr(self.wms, "url", "")
        endpoint = endpoint if isinstance(endpoint, str) else ""
        return json.dumps([self.CACHE_VERSION, endpoint, layer, list(map(float, bbox)),
                           int(width), int(height), requested], separators=(",", ":"))

    def _disk_path(self, base):
        return self.cache_dir / (hashlib.sha256(self._disk_key(base).encode()).hexdigest() + ".png")

    @staticmethod
    def _valid_time(actual, requested):
        # None means the server reported no substitute. Preserve that fact;
        # never manufacture an independently reported acquisition timestamp.
        return actual is None or abs((actual - parse_time(requested)).total_seconds()) <= 120

    @staticmethod
    def _complete(raw, width, height):
        return (isinstance(raw, np.ndarray) and raw.shape == (height, width, 4)
                and raw.dtype == np.uint8 and bool(np.all(raw[..., 3] >= 128)))

    def _read_disk(self, base):
        if self.cache_dir is None:
            return None
        path = self._disk_path(base)
        try:
            if path.is_symlink() or time.time() - path.stat().st_mtime > self.CACHE_TTL_S:
                return None
            with Image.open(path) as img:
                info = img.info
                if (img.format != "PNG" or img.mode != "RGBA" or img.size != (base[2], base[3])
                        or info.get("rasad_cache_version") != str(self.CACHE_VERSION)
                        or info.get("rasad_request") != self._disk_key(base)
                        or info.get("rasad_requested") != base[4] or "rasad_actual" not in info):
                    raise ValueError("picture cache metadata mismatch")
                actual = parse_time(info["rasad_actual"]) if info["rasad_actual"] else None
                if not self._valid_time(actual, base[4]):
                    raise ValueError("picture cache time mismatch")
                img.load()
                raw = np.array(img)
            if not self._complete(raw, base[2], base[3]):
                raise ValueError("picture cache incomplete")
            return raw
        except FileNotFoundError:
            return None
        except Exception as exc:  # corrupted cache must not interrupt satellite monitoring
            log.debug("cached picture unreadable: %s", exc)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _write_disk(self, base, raw, actual):
        if (self.cache_dir is None or not self._complete(raw, base[2], base[3])
                or not self._valid_time(actual, base[4])):
            return
        temporary = None
        try:
            info = PngImagePlugin.PngInfo()
            info.add_text("rasad_cache_version", str(self.CACHE_VERSION))
            info.add_text("rasad_request", self._disk_key(base))
            info.add_text("rasad_requested", base[4])
            info.add_text("rasad_actual", iso(actual) if actual is not None else "")
            fd, temporary = tempfile.mkstemp(prefix=".rasad-picture-", suffix=".tmp", dir=self.cache_dir)
            os.close(fd)
            Image.fromarray(raw).save(temporary, format="PNG", pnginfo=info)
            os.replace(temporary, self._disk_path(base))
            self._prune_disk()
        except OSError as exc:
            log.debug("picture cache write failed: %s", exc)
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def _prune_disk(self):
        now = time.time()
        files = []
        for path in self.cache_dir.iterdir():
            try:
                owned = bool(self._CACHE_NAME.fullmatch(path.name))
                temporary = path.name.startswith(".rasad-picture-") and path.suffix == ".tmp"
                if path.is_symlink() or not (owned or temporary) or not path.is_file():
                    continue
                stat = path.stat()
                if now - stat.st_mtime > self.CACHE_TTL_S:
                    path.unlink(missing_ok=True)
                elif owned:
                    files.append((stat.st_mtime, stat.st_size, path))
            except FileNotFoundError:
                continue  # another prefetch thread may have just pruned the entry
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= self.CACHE_MAX_BYTES:
                break
            try:
                path.unlink(missing_ok=True)
                total -= size
            except FileNotFoundError:
                pass

    def prefetch(self, requests_):
        """Ask for several pictures at once (the server is slow per picture, not per request)."""
        from concurrent.futures import ThreadPoolExecutor
        # Red, blue and the old mean share one RGBA download, including at the day/night boundary.
        todo = {}
        for r in requests_:
            if (r[0], tuple(r[1]), r[2], r[3], iso(r[4]), "rgba") in self.cache:
                continue
            for part, _ in tile_requests(*r[:5]):   # a big picture is fetched tile by tile
                base = (part[0], tuple(part[1]), part[2], part[3], iso(part[4]))
                if base + ("rgba",) not in self.cache:
                    todo.setdefault(base, part)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda r: self.get(*r, 0), todo.values()))

    @contextmanager
    def cached_only(self):
        """Validate acquired rasters without spending another network budget."""
        previous = self._network_allowed
        self._network_allowed = False
        try:
            yield self
        finally:
            self._network_allowed = previous

    def _tiled(self, layer, bbox, W, H, t):
        """A big picture read as tiles and put together. Every tile is kept on disk by itself, so
        a slow server costs one tile again, not the whole picture."""
        parts = tile_requests(layer, bbox, W, H, t)
        rows = {}
        for (name, tile_box, w, h, when), (x0, y0) in parts:
            base = (name, tuple(tile_box), w, h, iso(when))
            raw_key = base + ("rgba",)
            if raw_key not in self.cache:
                self.get(name, tile_box, w, h, when, 0)
            raw = self.cache.get(raw_key)
            if raw is None:
                return None
            rows[(x0, y0)] = raw
        out = np.empty((H, W, 4), np.uint8)
        for (x0, y0), raw in rows.items():
            out[y0:y0 + raw.shape[0], x0:x0 + raw.shape[1]] = raw
        for (name, tile_box, w, h, when), _ in parts:   # the tiles are in the whole picture now
            base = (name, tuple(tile_box), w, h, iso(when))
            for key in [k for k in self.cache if k[:5] == base]:
                del self.cache[key]
        return out

    def get(self, layer, bbox, W, H, t, channel=None):
        if channel not in (None, 0, 1, 2):
            raise ValueError("RGB channel must be 0, 1 or 2")
        base = (layer, tuple(bbox), W, H, iso(t))
        raw_key = base + ("rgba",)
        key = base if channel is None else base + (channel,)
        if key in self.cache:
            return self.cache[key]
        if raw_key not in self.cache and (W > TILE_PX or H > TILE_PX):
            self.cache[raw_key] = self._tiled(layer, bbox, W, H, t)
        if raw_key not in self.cache:
            raw = self._read_disk(base)
            if raw is None and not self._network_allowed:
                return None
            if raw is None and self._network_allowed:
                for _ in range(self.attempts):
                    img, actual = self.wms.getmap(layer, bbox, W, H, t=t, quiet=True)
                    if img is not None:
                        break
                if not self._valid_time(actual, base[4]):
                    img = None  # the server gave another time: this slot is not published yet
                raw = None if img is None else np.asarray(img)
                if raw is not None and raw.shape != (H, W, 4):
                    raw = None  # pixels from another grid cannot be geolocated using this request
                if raw is not None:
                    self._write_disk(base, raw, actual)
            self.cache[raw_key] = raw
        if key not in self.cache:
            self.cache[key] = _gray(self.cache[raw_key], channel)
        return self.cache[key]


TILE_PX = 1100   # the server takes ~20 s for a 2000 x 2000 picture and a few seconds for a quarter of it


def tile_requests(layer, bbox, W, H, t):
    """[(request, (x0, y0))]: the picture itself when small, otherwise its tiles in pixel-exact
    pieces (each piece is its own request, on the same grid)."""
    if W <= TILE_PX and H <= TILE_PX:
        return [((layer, tuple(bbox), W, H, t), (0, 0))]
    w, s, e, n = bbox
    nx, ny = math.ceil(W / TILE_PX), math.ceil(H / TILE_PX)
    xs = [round(i * W / nx) for i in range(nx + 1)]
    ys = [round(j * H / ny) for j in range(ny + 1)]
    out = []
    for j in range(ny):
        for i in range(nx):
            box = (w + xs[i] / W * (e - w), n - ys[j + 1] / H * (n - s),
                   w + xs[i + 1] / W * (e - w), n - ys[j] / H * (n - s))
            out.append(((layer, box, xs[i + 1] - xs[i], ys[j + 1] - ys[j], t), (xs[i], ys[j])))
    return out


_SEA = {}


def sea_grid(bbox, W, H, coast_km, km):
    """True where the pixel is open sea, at least coast_km from any land (1 km land mask)."""
    key = (tuple(bbox), W, H, coast_km)
    if key not in _SEA:
        mask = land_mask_points()
        if mask is None:
            _SEA[key] = None
        else:
            w, s, e, n = bbox
            lons = w + (np.arange(W) + 0.5) / W * (e - w)
            lats = n - (np.arange(H) + 0.5) / H * (n - s)
            gl, go = np.meshgrid(lats, lons, indexing="ij")
            land = mask(gl.ravel(), go.ravel()).reshape(H, W).astype(np.float64)
            _SEA[key] = _win_sum(land, max(1, int(math.ceil(coast_km / km)))) == 0
    return _SEA[key]


def _night(box, t, max_elev):
    w, s, e, n = box
    return [sun_elevation(la, lo, t) <= max_elev for la in (s, n) for lo in (w, e)]


def _ir105_change(pics, cfg, lat, lon, t, refs):
    """How sharply the 10.5 micron picture changed at the spot, in units of its local noise."""
    C = cfg["seawatch"]
    bb = box_around(lat, lon, 15)
    W, H = 31, 31
    cur = pics.get(C["check_layer"], bb, W, H, t)
    old = [pics.get(C["check_layer"], bb, W, H, t - dt.timedelta(minutes=m)) for m in refs]
    if cur is None or any(o is None for o in old):
        return None
    d = cur - np.nanmean(np.stack(old), axis=0)
    c = d[13:18, 13:18]
    med = float(np.nanmedian(d))
    sig = robust_sigma(d) or 0.5
    return round(float(np.nanmax(np.abs(c - med))) / max(sig, 0.5), 1)


def above_clear_sea(cur, sea, r, c, km, inner_km=2.0, outer_km=15.0):
    """How much warmer the spot is now than the clear sea around it: the warm end (90th
    percentile) of the open-sea pixels 2-15 km away. None when there is too little sea to tell.
    A fire stands above it; a gap opening in low cloud or fog only brings the spot back to it."""
    k = int(round(outer_km / km))
    r0, c0 = max(0, r - k), max(0, c - k)
    win, wsea = cur[r0:r + k + 1, c0:c + k + 1], sea[r0:r + k + 1, c0:c + k + 1]
    yy, xx = np.mgrid[r0:r0 + win.shape[0], c0:c0 + win.shape[1]]
    d = np.hypot(yy - r, xx - c) * km
    ring = wsea & (d >= inner_km) & (d <= outer_km) & ~np.isnan(win)
    if ring.sum() < 30:
        return None
    peak = float(np.nanmax(cur[max(0, r - 1):r + 2, max(0, c - 1):c + 2]))
    return peak - float(np.percentile(win[ring], 90))


def warm_extent_km(rise, r, c, km, frac=0.3, floor=2.0, limit_km=20.0):
    """How far the warming around the spot runs (touching pixels that clearly warmed too: at least
    30% as much, and 2 levels): a few km for a fire, whose surroundings do not change, but tens of
    km along the trailing edge of a drifting cloud band."""
    level = max(frac * float(rise[r, c]), floor)
    k = int(round(limit_km / km))
    r0, c0 = max(0, r - k), max(0, c - k)
    m = rise[r0:r + k + 1, c0:c + k + 1] >= level   # NaN compares False
    H, W = m.shape
    sr, sc = r - r0, c - c0
    seen = np.zeros_like(m)
    seen[sr, sc] = True
    stack, lo_r, hi_r, lo_c, hi_c = [(sr, sc)], sr, sr, sc, sc
    while stack:
        y, x = stack.pop()
        if y in (0, H - 1) or x in (0, W - 1):
            return limit_km   # runs out of the window: a long band
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if m[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
                    lo_r, hi_r, lo_c, hi_c = min(lo_r, ny), max(hi_r, ny), min(lo_c, nx), max(hi_c, nx)
    return (max(hi_r - lo_r, hi_c - lo_c) + 1) * km


def box_size(box, km):
    w, s, e, n = box["bbox"]
    wkm = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    return max(50, int(round(wkm / km))), max(50, int(round((n - s) * 111.32 / km)))


def restless_share(rise, sea, r, c, km, inner_km=3.0, outer_km=15.0, level=3.0):
    """How restless the sea around a spot is: the share of open-sea pixels 3-15 km away whose
    value changed by 3 levels or more. Next to nothing around a fire (0.00-0.03 on the real fires
    of 1, 13 and 14 November 2026); large where clouds drift (0.05-0.9 for every false speck of
    the cloudy night of 11 November). 1.0 when there is too little sea around to tell."""
    k = int(round(outer_km / km))
    r0, c0 = max(0, r - k), max(0, c - k)
    win, wsea = rise[r0:r + k + 1, c0:c + k + 1], sea[r0:r + k + 1, c0:c + k + 1]
    yy, xx = np.ogrid[r0:r0 + win.shape[0], c0:c0 + win.shape[1]]
    d = np.hypot(yy - r, xx - c) * km
    ring = wsea & (d >= inner_km) & (d <= outer_km) & ~np.isnan(win)
    if int(ring.sum()) < 50:
        return 1.0
    return float((np.abs(win[ring]) >= level).mean())


def stronger_source_near(cur, ref, sea, r, c, km, halo_km=10.0, margin=30.0, own_km=2.0):
    """Is the spot inside the glow of a much stronger source? A big flare (Das Island reads 115 to
    254 against a sea of 18) lights the pixels for kilometres around it, and they brighten and
    fade with it: four first alerts of 14 November 2026 were exactly that."""
    k = int(round(halo_km / km))
    r0, c0 = max(0, r - k), max(0, c - k)
    hot = np.fmax(cur[r0:r + k + 1, c0:c + k + 1], ref[r0:r + k + 1, c0:c + k + 1])
    wsea = sea[r0:r + k + 1, c0:c + k + 1]
    yy, xx = np.ogrid[r0:r0 + hot.shape[0], c0:c0 + hot.shape[1]]
    d = np.hypot(yy - r, xx - c) * km
    outside = hot[(d > own_km) & (d <= halo_km) & ~np.isnan(hot)]
    if not outside.size:
        return False
    calm = hot[wsea & ~np.isnan(hot)]
    background = float(np.median(calm)) if calm.size else float(np.nanmedian(hot))
    top = float(outside.max())
    return top - background >= margin and top > float(cur[r, c]) + 5


def _baseline(frames):
    """What the spot looked like before: the middle of three earlier pictures (a fire that is in
    one of them still shows as new; a passing cloud in one is ignored), else the warmest."""
    if len(frames) >= 3:
        return np.median(np.stack(frames), axis=0).astype(np.float32)
    out = frames[0]
    for f in frames[1:]:
        out = np.fmax(out, f)
    return out


def _grow3(a):
    """Each pixel's largest value among itself and its 8 neighbours (the short-wave colours are
    sampled on another grid than the 3.8 micron one: a glow may sit one pixel aside)."""
    p = np.pad(a, 1, mode="edge")
    out = a
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy != 1 or dx != 1:
                out = np.fmax(out, p[dy:dy + a.shape[0], dx:dx + a.shape[1]])
    return out


def find_spots(cfg, pics, box, t, mem=None, geo=None, refs=None):
    """Specks in one sea box at one slot. Returns (spots, notes) or (None, reason) if not ready.
    `refs`: minutes back to compare with (default the configured ones; other gaps are used when
    the source lost the usual reference pictures). All numbers are levels (0-255) of the red
    colour of the picture, never Kelvin or MW."""
    C = cfg["seawatch"]
    km = float(C["km_per_px"])
    W, H = box_size(box, km)
    if not any(_night(box["bbox"], t, float(C["sun_max_elev"]))):
        return [], "daylight"
    refs = [int(m) for m in (refs or C["ref_minutes"])]
    # a reference from before dusk still carries sunlight in this colour: prefer the night ones
    dark = [m for m in refs if all(_night(box["bbox"], t - dt.timedelta(minutes=m), float(C["sun_max_elev"])))]
    refs = dark or refs[:1]
    layer, bbox = C["layer"], box["bbox"]
    cur = pics.get(layer, bbox, W, H, t, 0)
    old = [pics.get(layer, bbox, W, H, t - dt.timedelta(minutes=m), 0) for m in refs]
    if cur is None or any(o is None for o in old):
        return None, "picture not ready"
    sea = sea_grid(bbox, W, H, float(C["min_coast_km"]), km)
    if sea is None:
        return [], "no land mask"
    ref = _baseline(old)
    rise = cur - ref
    # window sizes are in km, so the finder behaves the same at any picture detail
    spot = rise - local_mean(rise, max(2, round(5 / km)))     # what warmed more than its surroundings
    was_hot = ref - local_mean(ref, max(4, round(12 / km)))   # already warm before (a flare, a plant)
    # green and blue (2.2 and 1.6 micron) are dark at night except over flames
    glow = None
    gmin = float(C.get("flame_glow", 2.0))
    if gmin > 0:
        def short_wave(when):
            g, b = pics.get(layer, bbox, W, H, when, 1), pics.get(layer, bbox, W, H, when, 2)
            return None if g is None or b is None else np.fmax(g, b)
        frames = [short_wave(t)] + [short_wave(t - dt.timedelta(minutes=m)) for m in refs]
        if all(f is not None for f in frames):
            glow = _grow3(frames[0] - _baseline(frames[1:]))
    max_area = float(C["max_area_km2"]) / (km * km)
    sig = robust_sigma(np.where(sea, spot, np.nan))
    if sig is None:
        return [], "no sea in view"
    zmin, rmin, fmin = float(C["min_z"]), float(C["min_rise"]), float(C.get("flame_rise", 4.0))
    limit = int(C.get("max_candidate_px", 20000))

    def candidates(scale):
        m = (spot >= max(rmin, zmin * sig) * scale) & (rise >= rmin * scale)
        if glow is not None:   # a smaller warming counts when a flame's glow came with it
            m |= (spot >= max(fmin, 0.5 * zmin * sig) * scale) & (rise >= fmin * scale) & (glow >= gmin)
        return m & sea & ~np.isnan(spot)

    scale, cand = 1.0, candidates(1.0)
    while int(cand.sum()) > limit and scale < 6:   # a sky full of cloud edges: look only at the strongest
        scale *= 1.5
        cand = candidates(scale)
    out, notes = [], {"sigma": round(sig, 2), "candidates": 0, "rejected": {}}
    if scale > 1:
        notes["crowded"] = round(scale, 2)
    if int(cand.sum()) > limit:   # still a sky full of them: nothing in this picture can be trusted
        notes["rejected"]["too many warming pixels (cloud everywhere)"] = 1
        return out, notes
    if refs != [int(m) for m in C["ref_minutes"]]:
        notes["compared_with_min_before"] = refs

    def reject(why):
        notes["rejected"][why] = notes["rejected"].get(why, 0) + 1

    mask_fn = land_mask_points()
    strong_rise, strong_glow = float(C.get("strong_rise", 30.0)), float(C.get("strong_glow", 10.0))
    min_area = int(C.get("min_area_px", 4))
    for pts in components(cand, limit=limit):
        notes["candidates"] += 1
        if len(pts) > max_area:
            reject("too big (clouds)")
            continue
        vals = spot[pts[:, 0], pts[:, 1]]
        if len(pts) < min_area and float(vals.max()) < 2 * fmin:
            # one of the satellite's own pixels covers some twenty of the picture's: a lone
            # picture pixel just at the threshold is the noise floor (or a pilot flame's flicker)
            reject("a lone pixel at the threshold (noise)")
            continue
        k = int(np.argmax(vals))
        r, c = int(pts[k, 0]), int(pts[k, 1])
        lat, lon = px_to_latlon(bbox, W, H, c, r)
        if was_hot[r, c] > float(C["max_was_hot"]):
            reject("hot before (flare)")
            continue
        # the usual false speck at night: low cloud or fog drifting off makes the sea behind it
        # look warmer at 3.8 micron. A fire is hotter than the clear sea around it, and its
        # warming is a small speck, not a band tens of km long.
        above = above_clear_sea(cur, sea, r, c, km)
        if above is not None and above < float(C.get("min_above_sea", 3.0)):
            reject("no warmer than the clear sea (cloud or fog moving off)")
            continue
        extent = warm_extent_km(rise, r, c, km)
        if extent > float(C.get("max_warm_extent_km", 8.0)):
            reject("part of a long warming band (cloud edge)")
            continue
        if sun_elevation(lat, lon, t) > float(C["sun_max_elev"]):
            reject("daylight")
            continue
        shine = 0.0 if glow is None else float(np.nanmax(glow[max(0, r - 2):r + 3, max(0, c - 2):c + 3]))
        mighty = float(rise[r, c]) >= strong_rise and shine >= strong_glow   # a blaze, not a speck
        restless = restless_share(rise, sea, r, c, km)
        if restless > float(C.get("max_restless_strong" if mighty else "max_restless", 0.05)):
            reject("restless sea around it (clouds moving)")
            continue
        if stronger_source_near(cur, ref, sea, r, c, km, float(C.get("halo_km", 10.0)),
                                float(C.get("halo_margin", 30.0))):
            reject("glow of a stronger source nearby (flare)")
            continue
        if mask_fn is None:
            reject("no land mask")
            continue
        la, lo = ring_points(lat, lon, (0.0, float(C["min_coast_km"]) / 2, float(C["min_coast_km"])))
        if mask_fn(la, lo).any():
            reject("land or shore")
            continue
        if geo is not None and geo.platform_within(lat, lon, float(C["platform_km"])):
            reject("oil platform")
            continue
        # Recurring heat belongs to its own cell. One old nearby ship or an
        # onshore flare must not hide a new, separate hot spot in open water.
        night = (t - dt.timedelta(hours=12)).date()
        if persistent_local_heat(mem, lat, lon, night, C):
            reject("heat seen here before")
            continue
        z10 = _ir105_change(pics, cfg, lat, lon, t, refs[:1])
        notes.setdefault("z10", []).append(z10)
        if z10 is not None and z10 >= float(C["max_z_ir105"]):
            reject("cloud change (10.5 micron)")
            continue
        flame = shine >= gmin
        out.append({"src": SRC, "family": "mtg", "kind": "geo", "t": iso(t), "lat": round(lat, 4),
                    "lon": round(lon, 4), "frp": None, "rise": round(float(vals[k]), 1),
                    "z": round(float(vals[k]) / sig, 1), "z10": z10, "area_px": int(len(pts)),
                    "above_sea": None if above is None else round(above, 1), "extent_km": round(extent, 1),
                    "glow": round(shine, 1), "flame": bool(flame), "restless": round(restless, 3),
                    # clear enough to be told at once, before a second picture agrees
                    "strong": bool(float(rise[r, c]) >= float(C.get("early_min_rise", 15.0))
                                   and shine >= float(C.get("early_min_glow", 4.0)))})
    # fires at sea do not come by the dozen: many specks in one picture is the weather (low cloud
    # or fog thinning at night makes the sea look warmer at 3.8 micron); keep only the strongest
    burst = int(C.get("burst_max", 4))
    if len(out) > burst:
        strong = [sp for sp in out if sp["strong"] or (sp["z"] >= 2 * zmin and sp["rise"] >= 2 * rmin)]
        if len(out) - len(strong):
            notes["rejected"]["weather (many specks at once)"] = len(out) - len(strong)
        out = strong
    return out, notes


def confirm(cands, spot, km=3.0, minutes=40):
    """A speck counts only when another picture shows one at the same place within 40 minutes."""
    from .util import haversine_km
    t = parse_time(spot["t"])
    return [c for c in cands if c["t"] != spot["t"]
            and abs((t - parse_time(c["t"])).total_seconds()) <= minutes * 60
            and haversine_km(c["lat"], c["lon"], spot["lat"], spot["lon"]) <= km]


def watch(cfg, wms, state, now, mem=None, geo=None, avail=None):
    """Check recent slots, pairing candidates only within the same day/night detector.

    The experimental day detector requires both reference times and the cloud-check channel.
    Missing data stays pending, while day candidates never enter the night flare history.
    """
    import hashlib
    import json

    C = cfg["seawatch"]
    D = C.get("daytime", {})
    day_src = "mtg_day_spot"
    day_enabled = bool(D.get("enabled", False))
    if day_enabled:
        from .daywatch import find_spots as find_day_spots
    cad = int(C["cadence_min"])
    km = float(C["km_per_px"])
    day_km = float(D.get("km_per_px", km))   # the day finder's own picture detail
    sun = float(C["sun_max_elev"])
    platform_km = float(C["platform_km"])
    sparkle_min_days = max(1, int(C.get("sparkle_min_days", 3)))
    history_signature = history_rules(C)
    if (state.get("sea_platform_km") != platform_km
            or state.get("sea_sparkle_min_days") != sparkle_min_days
            or state.get("sea_history_rules") != history_signature):
        # Recheck recent pictures under changed filters; keep candidates and heat history.
        state["seawatch"] = {}
        state["sea_platform_km"] = platform_km
        state["sea_sparkle_min_days"] = sparkle_min_days
        state["sea_history_rules"] = history_signature
    done = state.setdefault("seawatch", {})
    cands = state.setdefault("sea_candidates", [])
    day_signature = hashlib.sha256(json.dumps({"version": 1, "config": D}, sort_keys=True,
                                             separators=(",", ":")).encode()).hexdigest()
    if state.get("sea_day_config") != day_signature:
        for key in [key for key in done if key.startswith("day:")]:
            del done[key]
        cands[:] = [c for c in cands if c.get("src", SRC) != day_src]
        state["sea_day_config"] = day_signature
        state.pop("sea_day_resume", None)
    day_status = {"enabled": day_enabled, "experimental": True, "checked": 0,
                  "missing": 0, "waiting": 0, "candidates": 0, "spots": 0,
                  "last_checked": max((v for k, v in done.items() if k.startswith("day:")), default=None),
                  "last_attempt": None}
    state["sea_day_status"] = day_status
    state_path = cfg.get("_state_dir")
    picture_options = {"attempts": 1} if cfg.get("_background_media") else {}
    if cfg.get("_background_media") and isinstance(state_path, (str, os.PathLike)):
        pics = Pictures(wms, cache_dir=Path(state_path).resolve().parent / ".rasad-pictures",
                        **picture_options)
    else:
        pics = Pictures(wms, **picture_options)
    avail = avail or Availability(wms, state, now)
    latest = floor_time(now - dt.timedelta(minutes=int(C["delay_min"])), cad)
    found = []
    live = [b for b in C["boxes"] if b.get("enabled", True)]
    if live:
        # A slow first region must not take every short cycle's acquisition budget.
        cursor = state.get("sea_day_box_cursor", 0)
        cursor = cursor if isinstance(cursor, int) else 0
        offset = cursor % len(live)
        live = live[offset:] + live[:offset]
        state["sea_day_box_cursor"] = (offset + 1) % len(live)
    sparkle = state.setdefault("sea_sparkle", {})  # ~5 km cell -> nights a speck was seen there
    span = max(int(C["slots_per_run"]), int(round(float(C.get("catchup_hours", 3)) * 60 / cad)))
    per_run, budget, t_start = int(C["slots_per_run"]), float(C.get("budget_s", 240)), time.time()
    # one tiny picture over the strait answers for the whole view; asked for at full detail,
    # because the server's coarse copy is not the picture the finder reads
    probe = box_around(26.3, 56.6, 1)
    todo = []
    for k in range(span):                # newest first
        t = latest - dt.timedelta(minutes=cad * k)
        night_boxes = [b for b in live if f"{b['id']}|{iso(t)}" not in done
                       and any(_night(b["bbox"], t, sun))]
        day_boxes = []
        if day_enabled:
            for box in live:
                if not box.get("day", True) or f"day:{box['id']}|{iso(t)}" in done:
                    continue
                w, s, e, n = box["bbox"]
                if any(sun_elevation(lat, lon, t) >= float(D.get("min_sun_elev", -6))
                       for lat in (s, n) for lon in (w, e)):
                    day_boxes.append(box)
        if night_boxes or day_boxes:
            todo.append((t, night_boxes, day_boxes))
    # At night the day finder has nothing to do: that is rest, not a fault.
    day_status["daylight"] = any(day_boxes for _, _, day_boxes in todo)
    resume = state.get("sea_day_resume", {})
    if not isinstance(resume, dict):
        resume = {}
    attempts = resume.get("attempts", 0)
    attempts = attempts if isinstance(attempts, int) else 0
    if attempts < 4:
        # Finish an acquired subset before a newer clock forces all six rasters
        # to change. A persistent source hole yields to fresh work after four tries.
        resume_job = next((job for job in todo if iso(job[0]) == resume.get("t") and job[2]), None)
        if resume_job is not None:
            todo.remove(resume_job)
            todo.insert(0, resume_job)
    # The priority seas (the Strait of Hormuz) come first, every picture of theirs not read yet, before
    # any other sea: when a check's time ran out, the boxes left were skipped, the box order rotates,
    # and a newer picture then came first. On 17 Nov 2026 the strait's 20:40 and 21:00 (Baghdad)
    # pictures were never read; a ship fire that showed from 21:00 went out from the 21:10 picture.
    prio = set(C.get("priority_boxes") or [])
    first = [(t, [b for b in nb if b["id"] in prio], [], True) for t, nb, _ in todo if any(b["id"] in prio for b in nb)]
    todo = first + [(t, [b for b in nb if b["id"] not in prio], db, False) for t, nb, db in todo]
    resume_candidate = None
    day_attempted = set()
    checked, waiting, lost = [], 0, 0
    for t, night_boxes, day_boxes, urgent in todo:
        if not night_boxes and not day_boxes:
            continue
        if (not urgent and len(checked) >= per_run) or time.time() - t_start > budget:
            waiting += 1
            day_status["waiting"] += len(day_boxes)
            continue
        if day_boxes and day_status["last_attempt"] is None:
            day_status["last_attempt"] = iso(t)
        night_ready = bool(night_boxes) and avail.has(C["layer"], t, probe)
        if night_boxes and not night_ready:
            if now - t > dt.timedelta(minutes=40):
                lost += 1    # a hole at the source (asked again now and then)
            else:
                waiting += 1  # not out yet
        # compare with the picture 30 minutes before, or the nearest one the source does have; just
        # after an outage that is the last picture before it, so a fire that began during the
        # outage still shows as new
        jobs = []
        if night_ready:
            wanted = [int(x) for x in C["ref_minutes"]]
            refs = [m for m in wanted if avail.has(C["layer"], t - dt.timedelta(minutes=m), probe)]
            if not refs:   # the usual references are lost at the source: the nearest other one
                refs = next(([m] for m in [40, 20, 50, 60, 70, 80, 90, 100, 110, 120] if m not in wanted
                             and avail.has(C["layer"], t - dt.timedelta(minutes=m), probe)), None)
            if refs is None:
                lost += 1
            else:
                jobs += [(box, SRC, refs) for box in night_boxes]
        if day_boxes:
            refs = [int(m) for m in D.get("ref_minutes", [30, 60])]
            # Download useful rasters directly. Six serial tiny availability
            # probes can consume the whole live budget and block valid disk caches.
            # The day finder still requires both references and all channels.
            if len(set(refs)) >= 2 and all(m > 0 for m in refs):
                jobs += [(box, day_src, refs) for box in day_boxes]
            else:
                day_status["missing"] += len(day_boxes)
                lost += 1
        if not jobs:
            continue
        if any(src == day_src for _, src, _ in jobs):
            day_attempted.add(iso(t))
        checked.append(t)
        for box, src, refs in jobs:
            is_day = src == day_src
            if not is_day and time.time() - t_start > budget:
                continue
            # One box at a time: a sea box at full detail is tens of megabytes per picture, and
            # seven of them times four picture times would not fit a small machine at once.
            size = box_size(box, day_km if is_day else km)
            requests = []
            for tt in [t] + [t - dt.timedelta(minutes=m) for m in refs]:
                requests.append((C["layer"], box["bbox"], *size, tt, 0))
                if is_day:
                    requests.append((C["check_layer"], box["bbox"], *size, tt))
            pics.prefetch(requests)
            key = ("day:" if is_day else "") + f"{box['id']}|{iso(t)}"
            finder = find_day_spots if is_day else find_spots
            if is_day:
                # A complete cached comparison is useful even when acquisition
                # consumed the deadline. Missing frames cannot trigger extra I/O.
                with pics.cached_only():
                    spots, notes = finder(cfg, pics, box, t, mem, geo, refs=refs)
            else:
                spots, notes = finder(cfg, pics, box, t, mem, geo, refs=refs)
            pics.cache = {}  # this box's pictures are done with
            if spots is None:
                log.info("sea watch %s %s: %s", box["id"], iso(t), notes)
                day_status["missing"] += int(is_day)
                if is_day and resume_candidate is None:
                    resume_candidate = iso(t)
                continue  # the download failed: try again next run
            done[key] = iso(t)
            if is_day:
                day_status["checked"] += 1
                day_status["candidates"] += len(spots)
                day_status["last_checked"] = max(day_status["last_checked"] or iso(t), iso(t))
            # One night spans midnight: adjacent pictures of a new fire must not
            # become prior-night evidence against each other.
            night = (t - dt.timedelta(hours=12)).date().isoformat()
            for sp in spots:
                pair = confirm([c for c in cands if c.get("src", SRC) == src], sp)
                width, height = box_size(box, day_km if is_day else km)
                cands.append(dict(
                    {k2: sp[k2] for k2 in ("t", "lat", "lon", "rise")}, src=src,
                    **({} if is_day else {"strong": bool(sp.get("strong")), "flame": bool(sp.get("flame"))}),
                    source_grid={"box_id": box["id"], "bbox": list(box["bbox"]),
                                 "width": width, "height": height,
                                 "km_per_px": day_km if is_day else km, "layer": C["layer"]},
                    reference_minutes=list(refs)))
                if is_day:
                    if pair:
                        found += [dict(row, src=day_src, family="mtg", kind="geo", frp=None,
                                       daytime=True, experimental=True) for row in pair + [sp]]
                    continue
                ci, cj = math.floor(sp["lat"] / 0.05), math.floor(sp["lon"] / 0.05)
                before = {d for di in (-1, 0, 1) for dj in (-1, 0, 1)
                          for d in sparkle.get(f"{ci + di},{cj + dj}", []) if d < night}
                sparkle.setdefault(f"{ci},{cj}", [])
                if night not in sparkle[f"{ci},{cj}"]:
                    sparkle[f"{ci},{cj}"].append(night)
                if pair and len(before) >= sparkle_min_days:
                    log.info("sea watch: specks at %.3f,%.3f again (also on %s): a flare, not news",
                             sp["lat"], sp["lon"], ", ".join(sorted(before)))
                elif pair:
                    found += [dict(row, src=SRC, family="mtg", kind="geo", frp=None) for row in pair + [sp]]
            if spots or (isinstance(notes, dict) and notes.get("candidates")):
                log.info("sea watch %s %s: %s", box["id"], iso(t),
                         notes if not spots else f"{len(spots)} speck(s) {notes}")
        pics.cache = {}  # pictures are big: keep memory low between slots
    if resume_candidate is not None:
        state["sea_day_resume"] = {"t": resume_candidate,
                                   "attempts": attempts + 1 if resume.get("t") == resume_candidate else 1}
    elif (resume.get("t") in day_attempted
          or not any(iso(t) == resume.get("t") and day for t, _, day, _u in todo)):
        state.pop("sea_day_resume", None)
    if checked or lost:
        log.info("sea watch: %d slot(s) checked%s, %d waiting, %d missing at the source", len(checked),
                 f" ({', '.join(f'{t:%H:%M}' for t in sorted(checked))})" if checked else "", waiting, lost)
    for key in [k for k, v in done.items() if now - parse_time(v) > dt.timedelta(hours=6)]:
        del done[key]
    keep_h = float(C.get("catchup_hours", 3)) + 1   # a late slot still pairs with its neighbours
    state["sea_candidates"] = [c for c in cands if now - parse_time(c["t"]) <= dt.timedelta(hours=keep_h)]
    oldest = (now - dt.timedelta(days=14)).date().isoformat()
    state["sea_sparkle"] = {k: [d for d in v if d >= oldest] for k, v in sparkle.items()
                            if any(d >= oldest for d in v)}
    uniq = {}
    for d in found:
        uniq[(d["src"], d["t"], d["lat"], d["lon"])] = d
    day_status["spots"] = sum(d["src"] == day_src for d in uniq.values())
    return list(uniq.values())


def look_back(cfg, wms, inc, mem=None, geo=None, hours_before=3.0, minutes_after=30, radius_km=6.0):
    """NASA reports a fire at sea hours later: when did it first show on Meteosat-12?
    Returns the specks found near it in the pictures before (and just after) NASA's pass."""
    from .util import haversine_km
    C = cfg["seawatch"]
    # a small area, so the full-detail picture can be asked for (big areas come back averaged)
    cfg = dict(cfg, seawatch=dict(C, km_per_px=float(C.get("look_back_km_per_px", 0.25))))
    cad = int(C["cadence_min"])
    first = parse_time(inc["first"])
    box = {"id": "lookback", "bbox": box_around(inc["lat"], inc["lon"], 30)}
    pics = Pictures(wms)
    t, end, found = floor_time(first - dt.timedelta(hours=hours_before), cad), \
        first + dt.timedelta(minutes=minutes_after), []
    while t <= end:
        spots, _ = find_spots(cfg, pics, box, t, mem, geo)
        found += [sp for sp in spots or []
                  if haversine_km(sp["lat"], sp["lon"], inc["lat"], inc["lon"]) <= radius_km]
        t += dt.timedelta(minutes=cad)
    return found


def hotspots(cfg, wms, start, end, bbox, top=8):
    """Diagnosis: for each slot, the strongest warmings anywhere in bbox (no filters), with whether
    the sea finder could have seen them (open sea at least min_coast_km from land, thresholds)."""
    C = cfg["seawatch"]
    cad, km = int(C["cadence_min"]), float(C["km_per_px"])
    box = {"id": "diag", "bbox": bbox}
    W, H = box_size(box, km)
    pics = Pictures(wms)
    sea = sea_grid(bbox, W, H, float(C["min_coast_km"]), km)
    t = floor_time(start, cad)
    while t <= end:
        refs = [t - dt.timedelta(minutes=int(m)) for m in C["ref_minutes"]]
        pics.prefetch([(C["layer"], bbox, W, H, tt) for tt in [t] + refs])
        cur = pics.get(C["layer"], bbox, W, H, t)
        old = [pics.get(C["layer"], bbox, W, H, r) for r in refs]
        if cur is None or any(o is None for o in old):
            log.info("%s: picture missing (%s)", iso(t), "now" if cur is None else "30 min before")
            t += dt.timedelta(minutes=cad)
            continue
        ref = old[0]
        rise = cur - ref
        spot = rise - local_mean(rise, max(2, round(5 / km)))
        sig = robust_sigma(np.where(sea, spot, np.nan)) if sea is not None else None
        hot = cur - local_mean(cur, max(4, round(12 / km)))     # warmer than its surroundings now
        near_sea = sea_grid(bbox, W, H, 1.0, km)
        b = np.where(np.isnan(hot) | ~near_sea, -1e9, hot)
        warm = []
        for _ in range(top):
            r, c = np.unravel_index(int(np.argmax(b)), b.shape)
            if b[r, c] < 3:
                break
            lat, lon = px_to_latlon(bbox, W, H, c, r)
            warm.append((round(lat, 4), round(lon, 4), round(float(hot[r, c]), 1), round(float(rise[r, c]), 1),
                         round(float(cur[r, c]), 1)))
            k = max(3, round(6 / km))
            b[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1] = -1e9
        log.info("%s: hottest at sea now (lat, lon, above surroundings, rise in 30 min, value): %s", iso(t), warm)
        a = np.where(np.isnan(spot), -1e9, spot)
        picked = []
        for _ in range(top):
            r, c = np.unravel_index(int(np.argmax(a)), a.shape)
            if a[r, c] < 2:
                break
            lat, lon = px_to_latlon(bbox, W, H, c, r)
            picked.append((round(lat, 4), round(lon, 4), round(float(spot[r, c]), 1), round(float(rise[r, c]), 1),
                           round(float(cur[r, c]), 1), bool(sea[r, c]) if sea is not None else None))
            k = max(3, round(6 / km))
            a[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1] = -1e9
        log.info("%s: sigma %s; strongest warmings (lat, lon, spot, rise, value, open sea): %s", iso(t),
                 None if sig is None else round(sig, 2), picked)
        pics.cache = {k2: v for k2, v in pics.cache.items() if k2[4] >= iso(t - dt.timedelta(minutes=40))}
        t += dt.timedelta(minutes=cad)


def scan(cfg, wms, start, end, mem=None, geo=None, boxes=None):
    """Replay the finder over a past period (for checking it before trusting it)."""
    C = cfg["seawatch"]
    cad = int(C["cadence_min"])
    pics = Pictures(wms)
    rows, t = [], floor_time(start, cad)
    while t <= end:
        km = float(C["km_per_px"])
        pics.prefetch([(C["layer"], b["bbox"], *box_size(b, km), tt) for b in C["boxes"]
                       if (not boxes or b["id"] in boxes) and any(_night(b["bbox"], t, float(C["sun_max_elev"])))
                       for tt in [t] + [t - dt.timedelta(minutes=int(m)) for m in C["ref_minutes"]]])
        for box in C["boxes"]:
            if (boxes and box["id"] not in boxes) or (not boxes and not box.get("enabled", True)):
                continue
            spots, notes = find_spots(cfg, pics, box, t, mem, geo)
            if spots:
                rows += [dict(sp, box=box["id"]) for sp in spots]
                for sp in spots:
                    near = geo.facility(sp["lat"], sp["lon"]) if geo is not None else None
                    log.info("   spot %.4f,%.4f rise %.1f z10 %s area %d px%s", sp["lat"], sp["lon"], sp["rise"],
                             sp["z10"], sp["area_px"], f"  nearest facility {near['km']} km ({near['label']})"
                             if near else "")
            if spots or (isinstance(notes, dict) and notes.get("candidates")):
                log.info("%s %s: %s %s", iso(t), box["id"], f"{len(spots)} spot(s)" if spots else "-", notes)
            elif spots is None:
                log.info("%s %s: %s", iso(t), box["id"], notes)
        # keep memory use small: drop pictures older than the references need
        oldest = t - dt.timedelta(minutes=max(int(m) for m in C["ref_minutes"]) + cad)
        pics.cache = {k: v for k, v in pics.cache.items() if k[4] >= iso(oldest)}
        t += dt.timedelta(minutes=cad)
    return rows


def scan_day(cfg, wms, start, end, mem=None, geo=None, boxes=None, km=None):
    """Replay the experimental day finder over a past period, at the picture detail asked for
    (``km`` per pixel; None = the detail it runs at). For measuring it before changing it: every
    slot and box prints what it found and why the rest was turned down."""
    import copy
    from .daywatch import find_spots as find_day_spots
    cfg = dict(cfg, seawatch=copy.deepcopy(cfg["seawatch"]))
    C = cfg["seawatch"]
    D = C.setdefault("daytime", {})
    if km:
        D["km_per_px"] = float(km)
    day_km = float(D.get("km_per_px", C.get("km_per_px", 0.5)))
    refs = [int(m) for m in D.get("ref_minutes", [30, 60])]
    cad = int(C["cadence_min"])
    rows = []
    for box in C["boxes"]:                     # one box at a time: full-detail pictures are big
        if boxes and box["id"] not in boxes:
            continue
        pics, size, t = Pictures(wms), box_size(box, day_km), floor_time(start, cad)
        while t <= end:
            requests = []
            for tt in [t] + [t - dt.timedelta(minutes=m) for m in refs]:
                requests += [(C["layer"], box["bbox"], *size, tt, 0), (C["check_layer"], box["bbox"], *size, tt)]
            pics.prefetch(requests)
            spots, notes = find_day_spots(cfg, pics, box, t, mem, geo, refs=refs)
            if spots:
                rows += [dict(sp, box=box["id"]) for sp in spots]
                for sp in spots:
                    near = geo.facility(sp["lat"], sp["lon"]) if geo is not None else None
                    log.info("   day spot %.4f,%.4f rise %s area %s px%s", sp["lat"], sp["lon"], sp.get("rise"),
                             sp.get("area_px"), f"  nearest facility {near['km']} km ({near['label']})" if near else "")
            log.info("%s %s at %.2f km: %s %s", iso(t), box["id"], day_km,
                     f"{len(spots)} spot(s)" if spots else ("-" if spots is not None else "not judged"), notes)
            t += dt.timedelta(minutes=cad)
            # the next slot uses this one's pictures as references: keep those, drop the older ones
            oldest = iso(t - dt.timedelta(minutes=max(refs)))
            pics.cache = {k: v for k, v in pics.cache.items() if k[4] >= oldest}
    rows.sort(key=lambda r: r["t"])
    return rows


def group(rows, km=4.0, minutes=40):
    """Spots of the same place within a short time = one would-be incident."""
    from .util import haversine_km
    inc = []
    for r in sorted(rows, key=lambda x: x["t"]):
        t = parse_time(r["t"])
        for g in inc:
            if haversine_km(r["lat"], r["lon"], g["lat"], g["lon"]) <= km and \
                    (t - parse_time(g["last"])).total_seconds() <= minutes * 60:
                g["last"] = r["t"]
                if r["t"] not in g["slots"]:
                    g["slots"].append(r["t"])
                g["max_rise"] = max(g["max_rise"], r["rise"])
                break
        else:
            inc.append({"lat": r["lat"], "lon": r["lon"], "first": r["t"], "last": r["t"], "slots": [r["t"]],
                        "max_rise": r["rise"], "box": r.get("box")})
    return inc
