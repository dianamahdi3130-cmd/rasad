"""Smoke as a second witness (silent for now: recorded, never sent, never changes a verdict).

By day a fire's smoke can show in Meteosat-12's visible pictures (500 m, every 10 minutes) as a
streak that starts at the fire. A streak alone proves nothing: small clouds look the same. So a
plume is accepted only when everything holds together:

  new        it was not there in the pictures before the heat was first seen;
  anchored   it starts at the heat's own place, in several pictures in a row (clouds drift);
  one way    it is long and narrow, lies to one side of that place, and keeps its direction;
  not cold   the thermal infrared (10.5 micron) barely changes along it (a cloud is cold);
  fair light the sun is high enough, no glint, and the scene around it is not full of new cloud.

The pictures are display values, not measurements: every threshold is relative to the picture's
own noise. ``find_plume`` is plain arithmetic on arrays (tested offline); ``check`` fetches the
pictures for one place and time, each request with a time limit.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .seawatch import local_mean, robust_sigma
from .solar import solar_geometry
from .util import direction_ar, floor_time, iso
from .wms import components

VERSION = 1
DEFAULTS = {
    "vis_layer": "mtg_fd:vis06_hrfi", "ir_layer": "mtg_fd:ir105_hrfi", "cadence_min": 10,
    "half_km": 40.0, "km_per_px": 0.5,
    "ref_minutes": [20, 30, 40],      # pictures before the heat was first seen: the scene without smoke
    "frames": 5,                      # pictures looked at, ending with the newest
    "min_frames": 3,                  # the plume must show in this many of them
    "background_km": 10.0,            # larger structures (haze, sun angle) are removed
    "k_sigma": 4.0, "min_contrast": 5.0,
    "anchor_km": 3.0,                 # the streak must start this close to the heat
    "min_length_km": 5.0, "min_elongation": 2.5, "max_width_km": 8.0,
    "source_end_share": 0.3,          # the heat sits within this share of the streak's length from one end
    "max_bearing_spread": 35.0,
    "max_ir_z": 3.0,                  # the streak in the thermal infrared, in units of that picture's noise
    "max_busy_share": 0.06,           # more new structure than this in the scene: clouds are moving in
    "min_sun_elev": 12.0, "min_glint_angle": 25.0,
}


def _high_pass(change, r):
    return change - local_mean(change, r)


def _streak(points, sy, sx, km, P):
    """Shape of one connected patch seen from the heat's place (sy, sx): None if not plume-like."""
    y, x = points[:, 0].astype(float), points[:, 1].astype(float)
    if len(y) < 6:
        return None
    cov = np.cov(np.stack([x - x.mean(), y - y.mean()]))
    values, vectors = np.linalg.eigh(cov)
    ux, uy = vectors[:, 1]                                   # the long axis
    along = (x - sx) * ux + (y - sy) * uy                    # measured from the heat
    across = -(x - sx) * uy + (y - sy) * ux
    lo, hi = float(along.min()), float(along.max())
    length = (hi - lo + 1) * km
    width = max(km, 4.0 * math.sqrt(max(float(values[0]), 0.0)) * km)
    if length < P["min_length_km"] or length / width < P["min_elongation"] or width > P["max_width_km"]:
        return None
    # the heat is at one end, and the streak passes it closely
    near = (hi - lo) * P["source_end_share"]
    if not (abs(lo) <= near or abs(hi) <= near) or lo > P["anchor_km"] / km or hi < -P["anchor_km"] / km:
        return None
    if float(np.abs(across[np.abs(along) <= P["anchor_km"] / km]).min(initial=1e9)) > P["anchor_km"] / km:
        return None
    sign = 1.0 if abs(hi) >= abs(lo) else -1.0               # which way the streak runs from the heat
    bearing = math.degrees(math.atan2(sign * ux, -sign * uy)) % 360     # x is east, y is south
    return {"length_km": round(length, 1), "width_km": round(width, 1), "bearing": round(bearing, 0), "pixels": int(len(y))}


def plume_in_picture(vis, vis_ref, ir, ir_ref, sy, sx, km, P):
    """One picture against the scene before the fire. Returns (streak or None, why not)."""
    ok = np.isfinite(vis) & np.isfinite(vis_ref)
    if ok.mean() < 0.9:
        return None, "incomplete picture"
    # the sun climbs or sinks between pictures: bring the earlier scene to this picture's brightness
    gain = float(np.clip(np.nanmedian(vis[ok]) / max(float(np.nanmedian(vis_ref[ok])), 1.0), 0.6, 1.6))
    change = _high_pass(np.where(ok, vis - vis_ref * gain, np.nan), max(2, int(round(P["background_km"] / km))))
    sigma = max(robust_sigma(change) or 1.0, 1.0)
    limit = max(P["k_sigma"] * sigma, P["min_contrast"])
    reach = P["anchor_km"] / km
    yy, xx = np.ogrid[:vis.shape[0], :vis.shape[1]]
    near_heat = (yy - sy) ** 2 + (xx - sx) ** 2 <= reach ** 2
    best, why = None, "no streak at the heat"
    for sign in (1.0, -1.0):                                 # smoke is brighter than sea, darker than sand
        mask = np.nan_to_num(sign * change) > limit
        if mask.mean() > P["max_busy_share"]:
            why = "the scene is changing everywhere (cloud)"
            continue
        if not (mask & near_heat).any():
            continue
        for points in components(mask):
            if not near_heat[points[:, 0], points[:, 1]].any():
                continue
            shape = _streak(points, sy, sx, km, P)
            if shape is None:
                why = "a patch at the heat, but not a streak from it"
                continue
            if ir is not None and ir_ref is not None:
                both = np.isfinite(ir) & np.isfinite(ir_ref)
                ir_change = _high_pass(np.where(both, ir - ir_ref, np.nan), max(2, int(round(P["background_km"] / km))))
                ir_sigma = max(robust_sigma(ir_change) or 1.0, 1.0)
                z = abs(float(np.nanmedian(ir_change[points[:, 0], points[:, 1]]))) / ir_sigma
                shape["ir_z"] = round(z, 1)
                if z > P["max_ir_z"]:
                    why = "cold in the thermal infrared: a cloud"
                    continue
            shape["contrast"] = round(float(np.nanmedian(np.abs(change[points[:, 0], points[:, 1]]))) / sigma, 1)
            shape["brighter"] = sign > 0
            if best is None or shape["length_km"] > best["length_km"]:
                best = shape
    return best, ("" if best else why)


def find_plume(vis, ir, refs_vis, refs_ir, sy, sx, km, settings=None):
    """The newest pictures (oldest first) against the scene before the fire. ``seen`` only when
    the same streak, from the same place, in the same direction, shows in enough of them."""
    P = {**DEFAULTS, **(settings or {})}
    vis_ref = np.nanmedian(np.stack(refs_vis), axis=0) if refs_vis else None
    ir_ref = np.nanmedian(np.stack(refs_ir), axis=0) if refs_ir else None
    out = {"seen": False, "frames": 0, "checked": 0, "why": "", "version": VERSION}
    if vis_ref is None:
        out["why"] = "no picture of the scene before the fire"
        return out
    found, reasons = [], []
    for i, picture in enumerate(vis):
        if picture is None:
            reasons.append("picture missing")
            continue
        out["checked"] += 1
        shape, why = plume_in_picture(picture, vis_ref, ir[i] if ir and ir[i] is not None else None, ir_ref, sy, sx, km, P)
        if shape:
            found.append(dict(shape, index=i))
        else:
            reasons.append(why)
    # the largest group of pictures whose streaks point the same way and are of the same kind
    best = []
    for a in found:
        group = [b for b in found if b["brighter"] == a["brighter"]
                 and abs((b["bearing"] - a["bearing"] + 180) % 360 - 180) <= P["max_bearing_spread"]]
        if len(group) > len(best):
            best = group
    out["frames"] = len(best)
    if len(best) >= P["min_frames"]:
        last = best[-1]
        out.update(seen=True, length_km=max(b["length_km"] for b in best), bearing=last["bearing"],
                   brighter=last["brighter"], contrast=max(b["contrast"] for b in best),
                   ir_z=max(b.get("ir_z", 0.0) for b in best))
    else:
        out["why"] = max(set(reasons), key=reasons.count) if reasons else ("seen in too few pictures" if found else "")
        if found and not out["why"]:
            out["why"] = "seen in too few pictures"
    return out


def daylight(lat, lon, when, P=DEFAULTS):
    sun, view, glint = solar_geometry(lat, lon, when)
    return bool(np.isfinite(sun) and sun >= P["min_sun_elev"] and np.isfinite(glint) and glint >= P["min_glint_angle"])


def check(cfg, pics, lat, lon, first, now):
    """Look for a plume from (lat, lon), whose heat was first seen at ``first``, in the newest
    pictures up to ``now``. Returns the finding (see ``find_plume``) with times and wording."""
    from .util import box_around
    P = {**DEFAULTS, **(cfg.get("smoke") or {})}
    step = dt.timedelta(minutes=int(P["cadence_min"]))
    newest = floor_time(now, P["cadence_min"])
    times = [newest - step * k for k in range(int(P["frames"]) - 1, -1, -1)]
    times = [t for t in times if t >= floor_time(first, P["cadence_min"])]
    out = {"seen": False, "frames": 0, "checked": 0, "why": "", "version": VERSION, "at": iso(newest)}
    if not times or not all(daylight(lat, lon, t, P) for t in (times[0], times[-1])):
        out["why"] = "not in fair daylight"
        return out
    bbox = box_around(lat, lon, P["half_km"])
    n = int(round(2 * P["half_km"] / P["km_per_px"]))
    ref_times = [floor_time(first, P["cadence_min"]) - dt.timedelta(minutes=int(m)) for m in P["ref_minutes"]]
    refs_vis = [p for p in (pics.get(P["vis_layer"], bbox, n, n, t) for t in ref_times) if p is not None]
    if len(refs_vis) < 2:
        out["why"] = "no picture of the scene before the fire"
        return out
    refs_ir = [p for p in (pics.get(P["ir_layer"], bbox, n, n, t) for t in ref_times) if p is not None]
    vis = [pics.get(P["vis_layer"], bbox, n, n, t) for t in times]
    ir = [pics.get(P["ir_layer"], bbox, n, n, t) for t in times]
    result = find_plume(vis, ir, refs_vis, refs_ir, (n - 1) / 2, (n - 1) / 2, P["km_per_px"], P)
    result["at"] = iso(newest)
    if result["seen"]:
        result["direction"] = direction_ar(result["bearing"])
    return result


def text(found):
    """One line for the app, in the caution the silent period needs."""
    if not found or not found.get("seen"):
        return ""
    return (f"دخان محتمل من موضع الحرارة (تجريبي): خط بطول نحو {found['length_km']:.0f} كم "
            f"يمتد باتجاه {found.get('direction') or 'واحد'}، ظهر في {found['frames']} صور متتالية")


# ---------------------------------------------------------------- silent recording
# The scanner writes a small job for a daytime fire and later copies the worker's finding onto
# the incident (``inc["smoke"]``): shown in the app, never posted, never part of the verdict.
def queue_dir(cfg):
    from pathlib import Path
    return Path(cfg["_state_dir"]).resolve().parent / ".rasad-smoke"


def _key(inc_id):
    import hashlib
    return hashlib.sha256(str(inc_id).encode()).hexdigest()[:40]


def wanted(inc, cfg, now):
    """Daytime fires that matter, while their smoke could still be seen."""
    from .util import parse_time
    P = {**DEFAULTS, **(cfg.get("smoke") or {})}
    if not P.get("enabled", True) or inc.get("recovered_historical"):
        return False
    done = inc.get("smoke") or {}
    if done.get("seen") or done.get("closed"):
        return False
    last = parse_time(inc["last"])
    if now - last > dt.timedelta(minutes=float(P.get("watch_minutes", 90))) or now < last:
        return False
    facility = inc.get("facility") or {}
    matters = (inc.get("place") in ("sea", "coast") or inc.get("status") == "alerted"
               or (facility.get("km") is not None and facility["km"] <= float(P.get("near_facility_km", 10)))
               or (inc.get("max_frp") or 0) >= float(P.get("min_mw", 20)))
    return bool(matters) and daylight(inc["lat"], inc["lon"], last, P)


def request(cfg, inc, now):
    from .util import load_json, save_json
    path = queue_dir(cfg) / "jobs" / (_key(inc["id"]) + ".json")
    job = {"id": inc["id"], "lat": inc["lat"], "lon": inc["lon"], "first": inc["first"], "last": inc["last"]}
    if {k: v for k, v in load_json(path, {}).items() if k in job} != job:
        save_json(path, dict(job, created_at=iso(now)), compact=True)


def collect(cfg, inc, now):
    """Copy the newest finding for this fire onto the incident. Returns True when it changed."""
    from .util import load_json, parse_time
    P = {**DEFAULTS, **(cfg.get("smoke") or {})}
    res = load_json(queue_dir(cfg) / "results" / (_key(inc["id"]) + ".json"), {})
    if res.get("id") != inc["id"] or "finding" not in res:
        if now - parse_time(inc["last"]) > dt.timedelta(minutes=float(P.get("watch_minutes", 90))) and inc.get("smoke"):
            inc["smoke"]["closed"] = True
        return False
    finding = dict(res["finding"])
    keep = {k: finding[k] for k in ("seen", "frames", "checked", "why", "at", "length_km", "bearing", "direction",
                                    "brighter", "contrast", "ir_z", "version") if k in finding}
    if (inc.get("smoke") or {}).get("at") == keep.get("at") and (inc.get("smoke") or {}).get("seen") == keep.get("seen"):
        return False
    inc["smoke"] = keep
    return True


def work_one(cfg, now, pics, wms=None):
    """Check the daytime fire that has waited longest. Returns its id, or None when nothing is due."""
    from .util import load_json, parse_time, save_json, log
    P = {**DEFAULTS, **(cfg.get("smoke") or {})}
    folder = queue_dir(cfg)
    (folder / "results").mkdir(parents=True, exist_ok=True)
    due = []
    for path in sorted((folder / "jobs").glob("*.json")):
        job = load_json(path, {})
        if not job.get("id"):
            continue
        if now - parse_time(job["last"]) > dt.timedelta(minutes=float(P.get("watch_minutes", 90))):
            path.unlink(missing_ok=True)                        # too late to see smoke: forget the job
            continue
        res = load_json(folder / "results" / path.name, {})
        if (res.get("finding") or {}).get("seen"):
            continue
        tried = res.get("tried_at") or ""
        if tried and now - parse_time(tried) < dt.timedelta(minutes=float(P.get("retry_minutes", 10))):
            continue
        due.append((tried, path.name, job))
    if not due:
        return None
    _, name, job = min(due)
    res = {"id": job["id"], "tried_at": iso(now)}
    try:
        if wms is not None:
            with wms.budget(float(P.get("job_budget_s", 150)), timeout_s=20):
                res["finding"] = check(cfg, pics, job["lat"], job["lon"], parse_time(job["first"]), now)
        else:
            res["finding"] = check(cfg, pics, job["lat"], job["lon"], parse_time(job["first"]), now)
        if res["finding"].get("seen"):
            log.info("smoke (silent) at %s: %s", job["id"], text(res["finding"]))
    except Exception as e:  # noqa: BLE001
        res["why"] = type(e).__name__
        log.warning("smoke check for %s failed: %s", job["id"], e)
    save_json(folder / "results" / name, res, compact=True)
    return job["id"]
