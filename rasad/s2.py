"""Sentinel-2 pictures of a place, read straight from the free archive.

The same engine as ``web/imagery.js`` (the app's «قبل وبعد»), for the picture the monitor sends
to the channel: the Earth Search index lists the passes, and only the tiles of the
Cloud-Optimized GeoTIFFs that cover the box are fetched, by HTTP range. Every request has a
time limit and the whole job a deadline. No key, no account, numpy only.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import struct
import time
import zlib

import numpy as np
import requests

from .util import UTC, iso, parse_time

SEARCH_URL = "https://earth-search.aws.element84.com/v1"
COLLECTIONS = ["sentinel-2-c1-l2a", "sentinel-2-l2a"]     # the reprocessed archive first
C1_FILES = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sentinel-2-c1-l2a/"
BAND_FILE = {"red": "B04", "green": "B03", "blue": "B02", "nir08": "B8A", "swir16": "B11",
             "swir22": "B12", "scl": "SCL"}
REFLECTANCE = ("red", "green", "blue", "nir08", "swir16", "swir22")
COLOR_BANDS = ("red", "green", "blue")
SWIR_BANDS = ("swir22", "nir08", "red")
CHANGE_BANDS = ("blue", "green", "red", "nir08", "swir22")
CHANGE_MIN = 0.045        # reflectance: smaller differences are haze, sun angle and sensor noise
CLEAR = 0.08              # a pass is clear when less than this share of the box is hidden
R_MERC = 6378137.0
HEAD_BYTES = 8192
FIELDS = {"include": ["id", "collection", "geometry", "properties.datetime", "properties.eo:cloud_cover",
                      "properties.platform", "properties.grid:code", "properties.proj:code",
                      "properties.proj:epsg"],
          "exclude": ["assets", "links", "stac_extensions", "stac_version", "bbox", "type"]}


class ArchiveError(RuntimeError):
    """code: timeout | source (the archive answered badly) | missing | format"""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------- UTM (WGS84, Krüger series)
_A, _F, _K0 = 6378137.0, 1 / 298.257223563, 0.9996
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_AL = (_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16, 13 * _N ** 2 / 48 - 3 * _N ** 3 / 5, 61 * _N ** 3 / 240)
_C = 2 * math.sqrt(_N) / (1 + _N)


def utm_forward(lat, lon, zone, south=False):
    """Latitude and longitude (numbers or arrays) to UTM easting and northing in one zone."""
    s = np.sin(np.radians(lat))
    dl = np.radians(np.asarray(lon, dtype=float) - (zone * 6 - 183))
    t = np.sinh(np.arctanh(s) - _C * np.arctanh(_C * s))
    xi, eta = np.arctan2(t, np.cos(dl)), np.arctanh(np.sin(dl) / np.sqrt(1 + t * t))
    e, n = eta.copy() if isinstance(eta, np.ndarray) else eta, xi.copy() if isinstance(xi, np.ndarray) else xi
    for j, a in enumerate(_AL, 1):
        e = e + a * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        n = n + a * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
    return 500000 + _K0 * _AA * e, (1e7 if south else 0) + _K0 * _AA * n


def projection_of(props):
    code = str(props.get("proj:code") or props.get("proj:epsg") or "").upper().replace("EPSG:", "")
    m = re.fullmatch(r"32([67])(\d\d)", code)
    if m:
        return {"zone": int(m.group(2)), "south": m.group(1) == "7"}
    m = re.match(r"MGRS-(\d{1,2})([C-X])", str(props.get("grid:code") or ""))
    if m:
        return {"zone": int(m.group(1)), "south": m.group(2) < "N"}
    raise ArchiveError("format", "unknown projection")


# ---------------------------------------------------------------- the picture grid
class Grid:
    """A square, north-up grid in the web map's projection, ``half_km`` to each side of the
    point: 10 m pixels, or larger for a big box."""

    def __init__(self, lat, lon, half_km, max_px=800):
        if not (abs(lat) < 80 and abs(lon) <= 180 and half_km > 0):
            raise ArchiveError("format", "bad place")
        span = 2 * half_km * 1000.0
        self.lat, self.lon, self.half_km = float(lat), float(lon), float(half_km)
        self.res = max(10.0, span / max_px)
        self.size = max(8, int(round(span / self.res)))
        self.step = self.res / math.cos(math.radians(lat))          # map metres per pixel
        half = self.size * self.step / 2
        self.mx0 = R_MERC * math.radians(lon) - half
        self.my0 = R_MERC * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) + half
        self.south, self.north = self.lat_of(self.my0 - 2 * half), self.lat_of(self.my0)
        self.west, self.east = math.degrees(self.mx0 / R_MERC), math.degrees((self.mx0 + 2 * half) / R_MERC)
        self._utm = {}

    @staticmethod
    def lat_of(my):
        return np.degrees(2 * np.arctan(np.exp(np.asarray(my) / R_MERC)) - math.pi / 2)

    def point(self, x, y):
        """Latitude and longitude of grid pixel (x, y); fractions allowed, (0, 0) = top-left corner."""
        return float(self.lat_of(self.my0 - y * self.step)), math.degrees((self.mx0 + x * self.step) / R_MERC)

    def utm(self, proj):
        """UTM easting and northing of every pixel centre, for one zone."""
        key = (proj["zone"], proj["south"])
        if key not in self._utm:
            i = np.arange(self.size) + 0.5
            lon = np.degrees((self.mx0 + i * self.step) / R_MERC)
            lat = self.lat_of(self.my0 - i * self.step)
            self._utm[key] = utm_forward(lat[:, None], lon[None, :], proj["zone"], proj["south"])
        return self._utm[key]


# ---------------------------------------------------------------- the sky
def sky_of(scl, ignore=None):
    """Shares of the box by ESA's scene classes (8/9 cloud, 3 its shadow, 10 thin cirrus, 0/1 nothing)."""
    n = scl.size
    nothing = (scl == 0) | (scl == 1)
    use = ~nothing if ignore is None else ~nothing & ~ignore
    cloud = float(((scl == 8) | (scl == 9))[use].sum()) / n
    shadow = float((scl == 3)[use].sum()) / n if cloud >= 0.002 else 0.0     # no cloud, no cloud shadow
    cirrus = float((scl == 10)[use].sum()) / n
    nodata = float(nothing.sum()) / n
    return {"cloud": cloud, "shadow": shadow, "cirrus": cirrus, "nodata": nodata,
            "hidden": cloud + shadow + cirrus * 0.5 + nodata}


def refine_sky(passes):
    """Bright ground (salt flats, white roofs) is often classed as cloud. A real cloud moves: a
    pixel called cloud in most of the passes is ground and stops counting against any pass."""
    seen = [p for p in passes if p.get("scl") is not None]
    if len(seen) < 6 or len({p["scl"].shape for p in seen}) != 1:
        return False
    times = sum(np.isin(p["scl"], (3, 8, 9, 10)).astype(np.int32) for p in seen)
    ignore = times > len(seen) * 0.5
    if not ignore.any():
        return False
    for p in seen:
        p["sky"] = sky_of(p["scl"], ignore)
    return True


def is_clear(p):
    return bool(p.get("sky")) and p["cover"] >= 0.985 and p["sky"]["hidden"] <= CLEAR


def pick_pair(passes, start, end):
    """The newest clear pass before the fire started and the first clear pass after its last
    sighting (``passes`` newest first; times as datetimes). Either can be None."""
    before = next((p for p in passes if p["time"] < start and is_clear(p)), None)
    after = next((p for p in reversed(passes) if p["time"] > end and is_clear(p)), None)
    return before, after


# ---------------------------------------------------------------- pictures
def stretch_of(scenes, bands):
    """One brightness range for both dates: the 0.5 % and 99.5 % points of the bands together."""
    values = []
    for s in scenes:
        if s is None:
            continue
        ok = ~np.isin(s["scl"], (0, 1, 3, 8, 9))
        for b in bands:
            v = s[b][ok]
            values.append(v[np.isfinite(v)][::max(1, v.size // 20000)])
    values = np.concatenate(values) if values else np.zeros(0)
    if values.size < 30:
        return 0.0, 0.4
    lo, hi = np.quantile(values, [0.005, 0.995])
    lo = max(0.0, min(float(lo), 0.15))
    return lo, max(float(hi), lo + 0.12)


def composite(scene, bands, stretch):
    """An RGBA picture (uint8) of three bands; transparent where the pass has nothing."""
    lo, hi = stretch
    stack = np.stack([scene[b] for b in bands], axis=-1)
    ok = np.isfinite(stack).all(axis=-1)
    v = np.clip((np.nan_to_num(stack) - lo) / (hi - lo), 0, 1) ** (1 / 1.7)
    out = np.zeros(stack.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.round(v * 255).astype(np.uint8)
    out[..., 3] = 255
    out[~ok] = 0
    return out


def _window_sum(mask, r):
    """How many set pixels in the square of radius r around every pixel, and the square's size."""
    n = mask.shape[0]
    c = np.zeros((n + 1, n + 1), np.int32)
    c[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.int32), axis=0), axis=1)
    i = np.arange(n)
    a, b = np.maximum(0, i - r), np.minimum(n, i + r + 1)
    count = c[b[:, None], b[None, :]] - c[a[:, None], b[None, :]] - c[b[:, None], a[None, :]] + c[a[:, None], a[None, :]]
    return count, (b - a)[:, None] * (b - a)[None, :]


def change_of(before, after, res):
    """Compare two scenes on the same grid (see ``changeOf`` in web/imagery.js: the same rules).
    Returns the changed mask, its direction, the area in hectares and how much could be compared."""
    a, b = before["scl"], after["scl"]
    total = a.size
    is_cloud = lambda c: (c == 8) | (c == 9)                                   # noqa: E731
    clouds = [float((is_cloud(a) & ~is_cloud(b)).sum()) / total > 0.003,
              float((is_cloud(b) & ~is_cloud(a)).sum()) / total > 0.003]
    skip = (a == 0) | (b == 0) | (a == 1) | (b == 1) | ~np.isfinite(before["red"]) | ~np.isfinite(after["red"])
    ca, cb = is_cloud(a) | ((a == 3) & clouds[0]), is_cloud(b) | ((b == 3) & clouds[1])
    veil = ~skip & ((ca != cb) | ((a == 10) != (b == 10)))
    low = lambda v: np.maximum(np.nan_to_num(v), 0.01)                          # noqa: E731
    for s, o, cloudy in ((before, after, clouds[0]), (after, before, clouds[1])):
        if not cloudy:
            continue
        bl, rd, obl = np.nan_to_num(s["blue"]), np.nan_to_num(s["red"]), np.nan_to_num(o["blue"])
        whitened = (bl > 0.2) & (bl - obl > 0.05) & (bl > rd * 0.72)
        rb, rr = bl / low(o["blue"]), rd / low(o["red"])
        rn, rs = np.nan_to_num(s["nir08"]) / low(o["nir08"]), np.nan_to_num(s["swir22"]) / low(o["swir22"])
        shaded = ((np.nan_to_num(o["nir08"]) > 0.1) & (rb < 0.9) & (rr < 0.85) & (rn < 0.85) & (rs < 0.85)
                  & (rs <= rb + 0.03) & (rn <= rb + 0.03))
        veil |= ~skip & (whitened | shaded)
    count, area = _window_sum(veil, max(2, int(round(60 / res))))
    solid = veil & (count >= 0.4 * area)
    wide = _window_sum(solid, max(1, int(round(120 / res))))[0] > 0
    skip = skip | veil | wide
    valid = int((~skip).sum())
    out = {"mask": np.zeros(a.shape, bool), "dir": np.zeros(a.shape, np.float32), "mag": np.zeros(a.shape, np.float32),
           "threshold": CHANGE_MIN, "compared": valid / total, "clouds": clouds[0] or clouds[1],
           "area_ha": 0.0, "dark_ha": 0.0, "bright_ha": 0.0, "share": 0.0}
    if valid < 50:
        return out
    mag, direction = np.zeros(a.shape, np.float64), np.zeros(a.shape, np.float64)
    for band in CHANGE_BANDS:
        A, B = np.nan_to_num(before[band]), np.nan_to_num(after[band])
        ma, mb = np.median(A[~skip]), np.median(B[~skip])
        gain = min(1.3, max(0.77, ma / mb)) if ma > 0.01 and mb > 0.01 else 1.0
        d = B * gain - A
        mag += d * d
        direction += d
    mag = np.sqrt(mag / len(CHANGE_BANDS))
    direction /= len(CHANGE_BANDS)
    med, q75 = np.quantile(mag[~skip], [0.5, 0.75])
    T = out["threshold"] = max(CHANGE_MIN, float(med + 6 * (q75 - med)))
    hot = ~skip & (mag > T)
    mask = hot & (_window_sum(hot, 1)[0] >= 4)            # a lone pixel or a thin line is noise
    ha = res * res / 1e4
    out.update(mask=mask, dir=direction.astype(np.float32), mag=mag.astype(np.float32),
               area_ha=float(mask.sum()) * ha, dark_ha=float((mask & (direction < 0)).sum()) * ha,
               bright_ha=float((mask & (direction >= 0)).sum()) * ha, share=float(mask.sum()) / valid)
    return out


def change_layer(change):
    """The change as a see-through RGBA layer: darker than before in red, brighter in cyan."""
    out = np.zeros(change["mask"].shape + (4,), np.uint8)
    T = change["threshold"]
    strength = np.clip((change["mag"] - T) / (2 * T), 0, 1)
    dark = change["mask"] & (change["dir"] < 0)
    bright = change["mask"] & (change["dir"] >= 0)
    out[dark, :3] = (255, 59, 48)
    out[bright, :3] = (34, 211, 238)
    out[..., 3] = np.where(change["mask"], 120 + 120 * strength, 0).astype(np.uint8)
    return out


def hot_count(scene):
    """How many separate places glow in the short-wave infrared (flames or gas flares)."""
    from .wms import components
    a, b, c = (np.nan_to_num(scene[k]) for k in ("swir22", "swir16", "nir08"))
    lb, lc = np.maximum(b, 0.01), np.maximum(c, 0.01)
    flag = ((a >= 0.15) & (a / lb >= 1.4) & (a / lc >= 1.4)) | ((b >= 0.5) & (b / lc >= 2) & (a >= 0.5))
    flag &= np.isfinite(scene["swir22"])
    return sum(1 for _ in components(flag)) if flag.any() else 0


# ---------------------------------------------------------------- the archive
class Archive:
    """Reads the index and the pictures. ``deadline`` (time.monotonic) bounds the whole job."""

    def __init__(self, session=None, deadline=None, search_url=SEARCH_URL, budgets=None):
        self.session = session or requests.Session()
        self.deadline = deadline
        self.search_url = search_url.rstrip("/")
        self.budgets = {"json": 25.0, "head": 20.0, "tile": 45.0, **(budgets or {})}
        self.bytes = 0
        self._cogs, self._tiles = {}, {}

    # -- network: every request has a time limit and one retry
    def _timeout(self, kind):
        limit = self.budgets[kind]
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0.5:
                raise ArchiveError("timeout", "the job's time is used up")
            limit = min(limit, left)
        return limit

    def _request(self, kind, method, url, **kw):
        last = None
        for _ in range(2):
            try:
                r = self.session.request(method, url, timeout=self._timeout(kind), **kw)
            except requests.Timeout:
                last = ArchiveError("timeout", "no answer in time")
                continue
            except requests.RequestException as e:
                last = ArchiveError("source", type(e).__name__)
                continue
            if r.status_code in (403, 404):
                raise ArchiveError("missing", f"HTTP {r.status_code}")
            if r.status_code not in (200, 206):
                last = ArchiveError("source", f"HTTP {r.status_code}")
                continue
            self.bytes += len(r.content)
            return r
        raise last

    def get_range(self, url, start, end, kind="tile"):
        r = self._request(kind, "GET", url, headers={"Range": f"bytes={start}-{end}"})
        if r.status_code != 206:            # never take a whole 200 MB file by accident
            raise ArchiveError("source", "range not served")
        return r.content

    def get_json(self, url, body=None):
        r = self._request("json", "POST" if body is not None else "GET", url, json=body)
        try:
            return r.json()
        except ValueError:
            raise ArchiveError("source", "bad answer") from None

    # -- Cloud-Optimized GeoTIFF: the header once, then only the tiles asked for
    def open_cog(self, url):
        if url in self._cogs:
            return self._cogs[url]
        chunks = [(0, self.get_range(url, 0, HEAD_BYTES - 1, "head"))]

        def view(off, size):
            for start, data in chunks:
                if off >= start and off + size <= start + len(data):
                    return data[off - start:off - start + size]
            data = self.get_range(url, off, off + max(size, HEAD_BYTES) - 1, "head")
            if len(data) < size:
                raise ArchiveError("format", "short file")
            chunks.append((off, data))
            return data[:size]

        head = view(0, 8)
        if head[:2] != b"II" or struct.unpack("<H", head[2:4])[0] != 42:
            raise ArchiveError("format", "not a little-endian TIFF")
        off, levels, geo = struct.unpack("<I", head[4:8])[0], [], None
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 12: 8}
        wanted = {254, 256, 257, 258, 259, 277, 284, 317, 322, 323, 324, 325, 339, 33550, 33922}
        for _ in range(24):
            if not off:
                break
            n = struct.unpack("<H", view(off, 2))[0]
            directory = view(off + 2, n * 12 + 4)
            tags = {}
            for i in range(n):
                tag, kind, count = struct.unpack("<HHI", directory[i * 12:i * 12 + 8])
                if tag not in wanted or kind not in sizes:
                    continue
                size = sizes[kind] * count
                raw = directory[i * 12 + 8:i * 12 + 8 + size] if size <= 4 else \
                    view(struct.unpack("<I", directory[i * 12 + 8:i * 12 + 12])[0], size)
                fmt = {1: "B", 2: "B", 3: "H", 4: "I", 12: "d"}[kind]
                tags[tag] = struct.unpack("<" + fmt * count, raw)
            off = struct.unpack("<I", directory[n * 12:n * 12 + 4])[0]
            one = lambda t, default=None: tags[t][0] if t in tags else default      # noqa: E731
            if one(254, 0) & 4:
                continue                                    # a transparency mask, not a picture
            lv = {"width": one(256), "height": one(257), "bits": one(258), "spp": one(277, 1),
                  "tile_w": one(322), "tile_h": one(323), "offsets": tags.get(324), "counts": tags.get(325),
                  "predictor": one(317, 1), "compression": one(259, 1)}
            if not lv["tile_w"] or not lv["offsets"] or not lv["counts"]:
                raise ArchiveError("format", "not a tiled TIFF")
            if (lv["compression"] not in (1, 8, 32946) or lv["bits"] not in (8, 16) or lv["spp"] != 1
                    or one(284, 1) != 1 or one(339, 1) != 1 or lv["predictor"] > 2):
                raise ArchiveError("format", "unsupported TIFF layout")
            lv["across"] = -(-lv["width"] // lv["tile_w"])
            if not levels:
                s, t = tags.get(33550), tags.get(33922)
                if not s or not t:
                    raise ArchiveError("format", "no georeference")
                geo = {"x0": t[3] - t[0] * s[0], "y0": t[4] + t[1] * s[1], "sx": s[0], "sy": s[1]}
                lv["sx"], lv["sy"] = geo["sx"], geo["sy"]
            else:
                lv["sx"] = geo["sx"] * levels[0]["width"] / lv["width"]
                lv["sy"] = geo["sy"] * levels[0]["height"] / lv["height"]
            levels.append(lv)
        if not levels:
            raise ArchiveError("format", "empty TIFF")
        cog = self._cogs[url] = {"url": url, "levels": levels, "x0": geo["x0"], "y0": geo["y0"]}
        return cog

    def read_tile(self, cog, li, tx, ty):
        key = (cog["url"], li, tx, ty)
        if key in self._tiles:
            return self._tiles[key]
        lv = cog["levels"][li]
        idx = ty * lv["across"] + tx
        dtype = np.uint16 if lv["bits"] == 16 else np.uint8
        count, offset = lv["counts"][idx], lv["offsets"][idx]
        if not count:
            tile = np.zeros((lv["tile_h"], lv["tile_w"]), dtype)             # an empty tile
        else:
            packed = self.get_range(cog["url"], offset, offset + count - 1)
            try:
                raw = packed if lv["compression"] == 1 else zlib.decompress(packed)
            except zlib.error:
                raise ArchiveError("format", "damaged tile") from None
            if len(raw) < lv["tile_w"] * lv["tile_h"] * lv["bits"] // 8:
                raise ArchiveError("format", "short tile")
            tile = np.frombuffer(raw, dtype="<u2" if lv["bits"] == 16 else np.uint8,
                                 count=lv["tile_w"] * lv["tile_h"]).reshape(lv["tile_h"], lv["tile_w"])
            if lv["predictor"] == 2:                         # rows stored as differences
                tile = np.cumsum(tile, axis=1, dtype=dtype)
        if len(self._tiles) > 64:
            self._tiles.pop(next(iter(self._tiles)))
        self._tiles[key] = tile
        return tile

    def sample_band(self, url, grid, proj, res, linear=False, scale=1.0, offset=0.0):
        """One band resampled onto the grid: reflectance (float32, NaN where nothing) when
        ``linear``, else the nearest class number (uint8)."""
        cog = self.open_cog(url)
        li = max([0] + [i for i, lv in enumerate(cog["levels"]) if i and lv["sx"] <= res * 1.02])
        lv = cog["levels"][li]
        E, N = grid.utm(proj)
        col, row = (E - cog["x0"]) / lv["sx"], (cog["y0"] - N) / lv["sy"]
        x0, y0 = max(0, int(math.floor(col.min())) - 2), max(0, int(math.floor(row.min())) - 2)
        x1 = min(lv["width"] - 1, int(math.ceil(col.max())) + 2)
        y1 = min(lv["height"] - 1, int(math.ceil(row.max())) + 2)
        if x1 < x0 or y1 < y0:
            return np.full(E.shape, np.nan, np.float32) if linear else np.zeros(E.shape, np.uint8)
        win = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.uint16 if lv["bits"] == 16 else np.uint8)
        for ty in range(y0 // lv["tile_h"], y1 // lv["tile_h"] + 1):
            for tx in range(x0 // lv["tile_w"], x1 // lv["tile_w"] + 1):
                tile = self.read_tile(cog, li, tx, ty)
                ox, oy = tx * lv["tile_w"], ty * lv["tile_h"]
                xa, xb = max(x0, ox), min(x1, ox + lv["tile_w"] - 1)
                ya, yb = max(y0, oy), min(y1, oy + lv["tile_h"] - 1)
                win[ya - y0:yb - y0 + 1, xa - x0:xb - x0 + 1] = tile[ya - oy:yb - oy + 1, xa - ox:xb - ox + 1]
        wh, ww = win.shape
        c, r = col - x0, row - y0
        inside = (c >= 0) & (r >= 0) & (c < ww) & (r < wh)
        if not linear:
            out = np.zeros(E.shape, np.uint8)
            out[inside] = win[np.floor(r[inside]).astype(int), np.floor(c[inside]).astype(int)]
            return out
        fx, fy = c - 0.5, r - 0.5
        x = np.clip(np.floor(fx).astype(int), 0, max(0, ww - 2))
        y = np.clip(np.floor(fy).astype(int), 0, max(0, wh - 2))
        ax, ay = np.clip(fx - x, 0, 1), np.clip(fy - y, 0, 1)
        xn, yn = np.minimum(x + 1, ww - 1), np.minimum(y + 1, wh - 1)
        p, q, s, t = win[y, x].astype(np.float64), win[y, xn].astype(np.float64), win[yn, x].astype(np.float64), win[yn, xn].astype(np.float64)
        blend = p * (1 - ax) * (1 - ay) + q * ax * (1 - ay) + s * (1 - ax) * ay + t * ax * ay
        nearest = np.where(ay < 0.5, np.where(ax < 0.5, p, q), np.where(ax < 0.5, s, t))
        full = (p > 0) & (q > 0) & (s > 0) & (t > 0)
        value = np.where(full, blend, nearest)               # at the swath edge: the nearest pixel
        out = np.where(inside & (value > 0), value * scale + offset, np.nan)
        return out.astype(np.float32)

    # -- the index: which passes cover the box
    def list_passes(self, grid, start, end):
        """The passes over the box between two datetimes, newest first, one per satellite per day."""
        body = {"collections": COLLECTIONS, "bbox": [grid.west, grid.south, grid.east, grid.north],
                "datetime": f"{iso(start)}/{iso(end)}", "limit": 500, "fields": FIELDS}
        items, request = [], body
        for _ in range(6):
            d = self.get_json(self.search_url + "/search", request)
            if not isinstance(d, dict) or not isinstance(d.get("features"), list):
                raise ArchiveError("source", "bad answer")
            items += d["features"]
            link = next((l for l in d.get("links") or [] if l.get("rel") == "next" and l.get("body")), None)
            if not link or not d["features"]:
                break
            request = {**body, **link["body"]} if link.get("merge") else link["body"]
        groups = {}
        for it in items:
            p = it.get("properties") or {}
            try:
                proj, when = projection_of(p), parse_time(p["datetime"])
            except (ArchiveError, KeyError, TypeError, ValueError):
                continue
            tile = {"id": it.get("id"), "collection": it.get("collection"), "datetime": p["datetime"], "time": when,
                    "platform": p.get("platform") or "", "grid": str(p.get("grid:code") or "").replace("MGRS-", ""),
                    "proj": proj, "tile_cloud": p.get("eo:cloud_cover"), "cover": _coverage(it.get("geometry"), grid)}
            if tile["id"] and tile["cover"] > 0:
                groups.setdefault((p["datetime"][:10], tile["platform"]), []).append(tile)
        weight = {}
        for g in groups.values():
            for t in g:
                weight[t["grid"]] = weight.get(t["grid"], 0) + t["cover"]
        rank = lambda t: t["cover"] * 1000 + (0.5 if t["collection"] == COLLECTIONS[0] else 0) + weight[t["grid"]] / 1e4   # noqa: E731
        passes = []
        for (day, platform), g in groups.items():
            best = max(g, key=rank)
            passes.append({**best, "key": f"{day}|{platform}", "date": day, "sky": None})
        passes.sort(key=lambda p: p["time"], reverse=True)
        return passes

    # -- where the files of a pass are
    def _assets(self, p, force=False):
        if p.get("assets") and not force:
            return p["assets"]
        m = re.fullmatch(r"(\d{1,2})([A-Z])([A-Z]{2})", p["grid"])
        if p["collection"] == COLLECTIONS[0] and m and not force:
            folder = (f"{C1_FILES}{int(m.group(1))}/{m.group(2)}/{m.group(3)}/{p['datetime'][:4]}/"
                      f"{int(p['datetime'][5:7])}/{p['id']}/")
            p["assets"] = {name: {"href": folder + file + ".tif", "scale": 1e-4, "offset": -0.1}
                           for name, file in BAND_FILE.items()}
            return p["assets"]
        d = self.get_json(f"{self.search_url}/collections/{p['collection']}/items/{p['id']}")
        assets = {}
        for name in BAND_FILE:
            a = (d.get("assets") or {}).get(name) or {}
            if not str(a.get("href") or "").startswith("https:"):
                raise ArchiveError("source", "pass without " + name)
            band = (a.get("raster:bands") or [{}])[0]
            assets[name] = {"href": a["href"], "scale": band.get("scale") or 1e-4, "offset": band.get("offset") or 0.0}
        p["assets"], p["assets_checked"] = assets, True
        return assets

    def _with_assets(self, p, work):
        try:
            return work(self._assets(p))
        except ArchiveError as e:
            if e.code != "missing" or p.get("assets_checked"):
                raise ArchiveError("source", str(e)) if e.code == "missing" else e
            return work(self._assets(p, force=True))            # the guessed addresses were wrong: ask the record

    def assess(self, p, grid):
        """The sky over the box itself, from ESA's scene classes at 20 m (a few kilobytes)."""
        if p.get("sky"):
            return p["sky"]
        sky_grid = Grid(grid.lat, grid.lon, grid.half_km, max(8, int(round(grid.half_km * 100))))
        p["scl"] = self._with_assets(p, lambda a: self.sample_band(a["scl"]["href"], sky_grid, p["proj"], 20))
        p["sky"] = sky_of(p["scl"])
        return p["sky"]

    def load_scene(self, p, grid, quality="full"):
        """The bands of one pass on the grid. "fast": everything at 20 m (a quarter of the download)."""
        res = max(20.0, grid.res) if quality == "fast" else grid.res

        def work(a):
            scene = {name: self.sample_band(a[name]["href"], grid, p["proj"], res, True, a[name]["scale"], a[name]["offset"])
                     for name in REFLECTANCE}
            scene["scl"] = self.sample_band(a["scl"]["href"], grid, p["proj"], 20)
            scene["pass"], scene["res"] = p, max(res, 10.0)
            return scene
        return self._with_assets(p, work)


def _in_ring(ring, lon, lat):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _coverage(geom, grid, k=9):
    """The share of the box inside the picture's real footprint (a pass can cut the box)."""
    if not isinstance(geom, dict):
        return 0.0
    polys = [geom.get("coordinates")] if geom.get("type") == "Polygon" else \
        geom.get("coordinates") if geom.get("type") == "MultiPolygon" else []
    hit = 0
    for j in range(k):
        for i in range(k):
            lat, lon = grid.point((i + 0.5) / k * grid.size, (j + 0.5) / k * grid.size)
            if any(p and _in_ring(p[0], lon, lat) and not any(_in_ring(h, lon, lat) for h in p[1:]) for p in polys or []):
                hit += 1
    return hit / (k * k)


def utc(value):
    """A datetime in UTC from an ISO text or a datetime."""
    t = parse_time(value) if isinstance(value, str) else value
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def days(n):
    return dt.timedelta(days=n)
