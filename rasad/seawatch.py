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
import time

import numpy as np

from .geodb import land_mask_points, ring_points
from .util import box_around, floor_time, iso, log, parse_time, px_to_latlon, sun_elevation
from .wms import Availability, components

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

    def prefetch(self, requests_):
        """Ask for several pictures at once (the server is slow per picture, not per request)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [r for r in requests_ if (r[0], tuple(r[1]), r[2], r[3], iso(r[4])) not in self.cache]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda r: self.get(*r), todo))

    def get(self, layer, bbox, W, H, t):
        key = (layer, tuple(bbox), W, H, iso(t))
        if key not in self.cache:
            img, actual = self.wms.getmap(layer, bbox, W, H, t=t, quiet=True)
            if img is None:  # big pictures sometimes fail once: ask again
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


def find_spots(cfg, pics, box, t, mem=None, geo=None, refs=None):
    """Specks in one sea box at one slot. Returns (spots, notes) or (None, reason) if not ready.
    `refs`: minutes back to compare with (default the configured ones; another gap is used when
    the source lost the usual reference picture)."""
    C = cfg["seawatch"]
    km = float(C["km_per_px"])
    W, H = box_size(box, km)
    if not any(_night(box["bbox"], t, float(C["sun_max_elev"]))):
        return [], "daylight"
    refs = [int(m) for m in (refs or C["ref_minutes"])]
    cur = pics.get(C["layer"], box["bbox"], W, H, t)
    old = [pics.get(C["layer"], box["bbox"], W, H, t - dt.timedelta(minutes=m)) for m in refs]
    if cur is None or any(o is None for o in old):
        return None, "picture not ready"
    sea = sea_grid(box["bbox"], W, H, float(C["min_coast_km"]), km)
    if sea is None:
        return [], "no land mask"
    ref = old[0]
    for o in old[1:]:
        ref = np.fmax(ref, o)
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
    if refs != [int(m) for m in C["ref_minutes"]]:
        notes["compared_with_min_before"] = refs

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
        # offshore oil fields flare on and off: no speck within ~10 km of heat NASA has seen before
        reach = max(1, int(round(float(C["known_heat_km"]) / 2.2)))
        if mem is not None and mem.profile(lat, lon, t.date().isoformat(), 30, reach)[0] > 0:
            reject("heat seen here before")
            continue
        z10 = _ir105_change(pics, cfg, lat, lon, t, refs)
        notes.setdefault("z10", []).append(z10)
        if z10 is not None and z10 >= float(C["max_z_ir105"]):
            reject("cloud change (10.5 micron)")
            continue
        out.append({"src": SRC, "family": "mtg", "kind": "geo", "t": iso(t), "lat": round(lat, 4),
                    "lon": round(lon, 4), "frp": None, "rise": round(float(vals[k]), 1),
                    "z": round(float(vals[k]) / sig, 1), "z10": z10, "area_px": int(len(pts)),
                    "above_sea": None if above is None else round(above, 1), "extent_km": round(extent, 1)})
    # fires at sea do not come by the dozen: many specks in one picture is the weather (low cloud
    # or fog thinning at night makes the sea look warmer at 3.8 micron); keep only the strongest
    burst = int(C.get("burst_max", 4))
    if len(out) > burst:
        strong = [sp for sp in out if sp["z"] >= 2 * zmin and sp["rise"] >= 2 * rmin]
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
    """Run the finder on the Meteosat-12 slots not looked at yet: the newest first, then any slot of
    the last few hours that was missed (the source was late or down, or a run was slow). Only
    pictures the source really has are downloaded. A speck is reported only once a second picture
    shows one at the same place (single specks are mostly noise)."""
    C = cfg["seawatch"]
    cad = int(C["cadence_min"])
    km = float(C["km_per_px"])
    sun = float(C["sun_max_elev"])
    platform_km = float(C["platform_km"])
    sparkle_min_days = max(1, int(C.get("sparkle_min_days", 3)))
    if (state.get("sea_platform_km") != platform_km
            or state.get("sea_sparkle_min_days") != sparkle_min_days):
        # Recheck recent pictures under changed filters; keep candidates and heat history.
        state["seawatch"] = {}
        state["sea_platform_km"] = platform_km
        state["sea_sparkle_min_days"] = sparkle_min_days
    done = state.setdefault("seawatch", {})
    cands = state.setdefault("sea_candidates", [])
    pics = Pictures(wms)
    avail = avail or Availability(wms, state, now)
    latest = floor_time(now - dt.timedelta(minutes=int(C["delay_min"])), cad)
    found = []
    live = [b for b in C["boxes"] if b.get("enabled", True)]
    sparkle = state.setdefault("sea_sparkle", {})  # ~5 km cell -> nights a speck was seen there
    span = max(int(C["slots_per_run"]), int(round(float(C.get("catchup_hours", 3)) * 60 / cad)))
    per_run, budget, t_start = int(C["slots_per_run"]), float(C.get("budget_s", 240)), time.time()
    probe = box_around(26.3, 56.6, 10)   # one tiny picture over the strait answers for the whole view
    todo = []
    for k in range(span):                # newest first
        t = latest - dt.timedelta(minutes=cad * k)
        if any(f"{b['id']}|{iso(t)}" not in done and any(_night(b["bbox"], t, sun)) for b in live):
            todo.append(t)
    checked, waiting, lost = [], 0, 0
    for t in todo:
        if len(checked) >= per_run or time.time() - t_start > budget:
            waiting += 1
            continue
        if not avail.has(C["layer"], t, probe):
            if now - t > dt.timedelta(minutes=40):
                lost += 1    # a hole at the source (asked again now and then)
            else:
                waiting += 1  # not out yet
            continue
        # compare with the picture 30 minutes before, or the nearest one the source does have; just
        # after an outage that is the last picture before it, so a fire that began during the
        # outage still shows as new
        refs = next(([m] for m in [int(x) for x in C["ref_minutes"]] + [40, 20, 50, 60, 70, 80, 90, 100, 110, 120]
                     if avail.has(C["layer"], t - dt.timedelta(minutes=m), probe)), None)
        if refs is None:
            lost += 1
            continue
        boxes = [b for b in live if f"{b['id']}|{iso(t)}" not in done and any(_night(b["bbox"], t, sun))]
        pics.prefetch([(C["layer"], b["bbox"], *box_size(b, km), tt) for b in boxes
                       for tt in [t] + [t - dt.timedelta(minutes=m) for m in refs]])
        checked.append(t)
        for box in boxes:
            key = f"{box['id']}|{iso(t)}"
            spots, notes = find_spots(cfg, pics, box, t, mem, geo, refs=refs)
            if spots is None:
                log.info("sea watch %s %s: %s", box["id"], iso(t), notes)
                continue  # the download failed: try again next run
            done[key] = iso(t)
            # One night spans midnight: adjacent pictures of a new fire must not
            # become prior-night evidence against each other.
            night = (t - dt.timedelta(hours=12)).date().isoformat()
            for sp in spots:
                pair = confirm(cands, sp)
                cands.append({k2: sp[k2] for k2 in ("t", "lat", "lon", "rise")})
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
                    found += [dict(sp, src=SRC, family="mtg", kind="geo", frp=None) for sp in pair + [sp]]
            if spots or (isinstance(notes, dict) and notes.get("candidates")):
                log.info("sea watch %s %s: %s", box["id"], iso(t),
                         notes if not spots else f"{len(spots)} speck(s) {notes}")
        pics.cache = {}  # pictures are big: keep memory low between slots
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
        uniq[(d["t"], d["lat"], d["lon"])] = d
    return list(uniq.values())


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
