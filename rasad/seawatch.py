"""Rasad's own hot-spot finder on Meteosat-12's Fire Temperature picture, every 10 minutes.

The official fire product only flags fires above roughly 20-50 MW at the angle Meteosat-12 sees
the Gulf, so a small ship fire (the Hormuz fire of 1 November 2026 was about 5 MW) is not flagged,
although it does show in the picture as a new warm speck. This finder looks for exactly that,
at night and at sea, where the background is calm. By day the sun glints on the water in the
3.8 micron channel, so daytime is left to the other sources.

A speck is kept only if:
  * it is warmer than the same spot 30 and 60 minutes earlier, by a clear margin, while the
    area around it did not warm the same way (clouds clearing warm a whole area);
  * it is small (a fire, not a sheet of clearing sky);
  * the spot was not already hot before (that would be a flare that flickers);
  * it is at sea, away from the shore, from oil platforms and from heat seen there before;
  * the 10.5 micron picture did not change there the same way (a hole in a cloud changes
    both pictures; a small fire changes almost only the 3.8 micron one).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .geodb import land_mask_points, ring_points
from .util import box_around, floor_time, iso, log, parse_time, px_to_latlon, sun_elevation
from .wms import components

SRC = "mtg_spot"


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


def _gray(img):
    if img is None:
        return None
    a = np.asarray(img)
    if a[..., 3].mean() < 30:
        return None  # nothing drawn: no data for that time
    g = a[..., :3].astype(np.float32).mean(axis=2)
    g[a[..., 3] < 128] = np.nan
    return g


def robust_sigma(x):
    v = x[~np.isnan(x)]
    if v.size < 50:
        return None
    med = np.median(v)
    return float(1.4826 * np.median(np.abs(v - med))) or 0.3


class Pictures:
    """Fetches and keeps the pictures needed in one run (the same slot serves several checks)."""

    def __init__(self, wms):
        self.wms, self.cache = wms, {}

    def get(self, layer, bbox, W, H, t):
        key = (layer, tuple(bbox), W, H, iso(t))
        if key not in self.cache:
            img, actual = self.wms.getmap(layer, bbox, W, H, t=t, quiet=True)
            if actual is not None and abs((actual - t).total_seconds()) > 120:
                img = None  # the server gave another time: this slot is not published yet
            self.cache[key] = _gray(img)
        return self.cache[key]


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


def find_spots(cfg, pics, box, t, mem=None, geo=None):
    """Specks in one sea box at one slot. Returns (spots, notes) or (None, reason) if not ready."""
    C = cfg["seawatch"]
    km = float(C["km_per_px"])
    w, s, e, n = box["bbox"]
    wkm = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    W = max(50, int(round(wkm / km)))
    H = max(50, int(round((n - s) * 111.32 / km)))
    if not any(_night(box["bbox"], t, float(C["sun_max_elev"]))):
        return [], "daylight"
    refs = [int(m) for m in C["ref_minutes"]]
    cur = pics.get(C["layer"], box["bbox"], W, H, t)
    old = [pics.get(C["layer"], box["bbox"], W, H, t - dt.timedelta(minutes=m)) for m in refs]
    if cur is None or any(o is None for o in old):
        return None, "picture not ready"
    sea = sea_grid(box["bbox"], W, H, float(C["min_coast_km"]), km)
    if sea is None:
        return [], "no land mask"
    ref = np.fmax(old[0], old[1]) if len(old) > 1 else old[0]
    rise = cur - ref
    # window sizes are in km, so the finder behaves the same at any picture detail
    spot = rise - local_mean(rise, max(2, round(5 / km)))     # what warmed more than its surroundings
    was_hot = ref - local_mean(ref, max(4, round(12 / km)))   # already warm before (a flare, a plant)
    max_area = float(C["max_area_km2"]) / (km * km)
    sig = robust_sigma(np.where(sea, spot, np.nan))
    if sig is None:
        return [], "no sea in view"
    zmin, rmin = float(C["min_z"]), float(C["min_rise"])
    cand = (spot >= max(rmin, zmin * sig)) & (rise >= rmin) & sea
    cand &= ~np.isnan(spot)
    out, notes = [], {"sigma": round(sig, 2), "candidates": 0, "rejected": {}}

    def reject(why):
        notes["rejected"][why] = notes["rejected"].get(why, 0) + 1

    mask_fn = land_mask_points()
    for pts in components(cand, limit=5000):
        notes["candidates"] += 1
        if len(pts) > max_area:
            reject("too big (clouds)")
            continue
        vals = spot[pts[:, 0], pts[:, 1]]
        k = int(np.argmax(vals))
        r, c = int(pts[k, 0]), int(pts[k, 1])
        lat, lon = px_to_latlon(box["bbox"], W, H, c, r)
        if was_hot[r, c] > float(C["max_was_hot"]):
            reject("hot before (flare)")
            continue
        if sun_elevation(lat, lon, t) > float(C["sun_max_elev"]):
            reject("daylight")
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
        if mem is not None and mem.profile(lat, lon, t.date().isoformat(), 30, 3)[0] > 0:
            reject("heat seen here before")
            continue
        z10 = _ir105_change(pics, cfg, lat, lon, t, refs)
        if z10 is not None and z10 >= float(C["max_z_ir105"]):
            reject("cloud change (10.5 micron)")
            continue
        out.append({"src": SRC, "family": "mtg", "kind": "geo", "t": iso(t), "lat": round(lat, 4),
                    "lon": round(lon, 4), "frp": None, "rise": round(float(vals[k]), 1),
                    "z": round(float(vals[k]) / sig, 1), "z10": z10, "area_px": int(len(pts))})
    return out, notes


def watch(cfg, wms, state, now, mem=None, geo=None):
    """Run the finder on the newest Meteosat-12 slots not looked at yet."""
    C = cfg["seawatch"]
    cad = int(C["cadence_min"])
    done = state.setdefault("seawatch", {})
    pics = Pictures(wms)
    latest = floor_time(now - dt.timedelta(minutes=int(C["delay_min"])), cad)
    found = []
    for k in range(int(C["slots_per_run"]) - 1, -1, -1):
        t = latest - dt.timedelta(minutes=cad * k)
        for box in C["boxes"]:
            key = f"{box['id']}|{iso(t)}"
            if key in done:
                continue
            spots, notes = find_spots(cfg, pics, box, t, mem, geo)
            if spots is None:
                continue  # not published yet: try again next run
            done[key] = iso(t)
            found += spots
            if spots or (isinstance(notes, dict) and notes.get("candidates")):
                log.info("sea watch %s %s: %s", box["id"], iso(t),
                         notes if not spots else f"{len(spots)} spot(s) {notes}")
    for key in [k for k, v in done.items() if now - parse_time(v) > dt.timedelta(hours=6)]:
        del done[key]
    return found


def look_back(cfg, wms, inc, mem=None, geo=None, hours_before=3.0, minutes_after=30, radius_km=6.0):
    """NASA reports a fire at sea hours later: when did it first show on Meteosat-12?
    Returns the specks found near it in the pictures before (and just after) NASA's pass."""
    from .util import haversine_km
    C = cfg["seawatch"]
    # a small area, so the full-detail picture can be asked for (big areas come back averaged)
    cfg = dict(cfg, seawatch=dict(C, km_per_px=float(C.get("look_back_km_per_px", 0.5))))
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


def scan(cfg, wms, start, end, mem=None, geo=None, boxes=None):
    """Replay the finder over a past period (for checking it before trusting it)."""
    C = cfg["seawatch"]
    cad = int(C["cadence_min"])
    pics = Pictures(wms)
    rows, t = [], floor_time(start, cad)
    while t <= end:
        for box in C["boxes"]:
            if boxes and box["id"] not in boxes:
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
                g["slots"].append(r["t"])
                g["max_rise"] = max(g["max_rise"], r["rise"])
                break
        else:
            inc.append({"lat": r["lat"], "lon": r["lon"], "first": r["t"], "last": r["t"], "slots": [r["t"]],
                        "max_rise": r["rise"], "box": r.get("box")})
    return inc
