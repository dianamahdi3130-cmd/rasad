"""A fire at sea by day that stays put: shown in the app with the day finder's own candidates,
never posted on its own (day evidence needs another satellite: brain.day_requires_confirmation).

By day the 3.8 micron picture over the sea is full of the sun's reflection and of sunlit cloud.
The day finder (daywatch.py) looks for heat that is new against 30 and 60 minutes before, and a
picture where too much changes is refused whole (around noon). A burning ship is different: it
stays at one place, picture after picture, clearly hotter than the sea around it, while the
reflection and the clouds move on. The case: 20 Nov 2026, a spot about 100 km north of Muscat
(24.48, 58.16) was 55-70 above its surroundings in every picture from 09:40 to 11:20 UTC while
the day finder refused those pictures ('too many anomalous pixels').
"""
from __future__ import annotations

import datetime as dt
import statistics

import numpy as np

from .util import haversine_km, iso, log, px_to_latlon

STEADY = {"enabled": True, "top": 60, "min_above": 35.0, "min_median": 50.0, "min_pictures": 4,
          "reach_km": 2.0, "usual_km": 3.0, "usual_share": 0.05, "keep_hours": 3,
          # sand or rock warmed by the sun (an island, a new artificial one the land map does not know)
          # is warmer at 10.5 micron too; a fire smaller than a pixel hardly is
          "max_warm105": None}


def settings(cfg):
    return dict(STEADY, **(cfg["seawatch"].get("daytime", {}).get("steady") or {}))


def hot_spots(cfg, red, bbox, km, ir=None):
    """The places of one picture clearly hotter than the sea around them: [lat, lon, above, warm105]
    (warm105: how much warmer than around it the place is at 10.5 micron, None without that picture;
    the picture is drawn bright where it is cold)."""
    from .seawatch import local_mean, sea_grid
    P = settings(cfg)
    H, W = red.shape
    sea = sea_grid(bbox, W, H, float(cfg["seawatch"].get("min_coast_km", 3)), km)
    if sea is None:
        return None
    above = red - local_mean(red, max(4, round(12 / km)))
    warm = None if ir is None or ir.shape != red.shape else local_mean(ir, max(4, round(12 / km))) - ir
    b = np.where(np.asarray(sea, dtype=bool) & np.isfinite(above), above, -1e9)
    out, k = [], max(3, round(6 / km))
    for _ in range(int(P["top"])):
        r, c = np.unravel_index(int(np.argmax(b)), b.shape)
        if b[r, c] < float(P["min_above"]):
            break
        lat, lon = px_to_latlon(bbox, W, H, c, r)
        w105 = None if warm is None else round(float(np.max(warm[max(0, r - 2):r + 3, max(0, c - 2):c + 3])), 1)
        out.append([round(float(lat), 4), round(float(lon), 4), round(float(above[r, c]), 1), w105])
        b[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1] = -1e9
    return out


def _usual(lat, lon, P, geo):
    """Heat that belongs there: a platform, or a spot NASA sees hot on many dates."""
    from . import patterns
    if geo is not None and geo.platform_within(lat, lon, float(P["usual_km"])):
        return True
    A = patterns.atlas()
    return bool(A) and patterns.usual_flare_km(A, lat, lon, float(P["usual_km"]), float(P["usual_share"])) is not None


def observe(cfg, state, box_id, t, spots, geo=None):
    """Note this picture's hot places; return one row per picture of every place that stayed hot in
    `min_pictures` pictures in a row (each row once)."""
    P = settings(cfg)
    cad = dt.timedelta(minutes=int(cfg["seawatch"]["cadence_min"]))
    rec = state.setdefault("sea_day_steady", {}).setdefault(box_id, {"read": {}, "told": []})
    rec["read"][iso(t)] = spots
    oldest = iso(t - dt.timedelta(hours=float(P["keep_hours"])))
    rec["read"] = {k: v for k, v in rec["read"].items() if k >= oldest}
    rec["told"] = [k for k in rec["told"] if k.split("|")[0] >= oldest]
    reach, rows = float(P["reach_km"]), []
    for spot in spots:
        lat, lon, above = spot[:3]
        run = [(iso(t), lat, lon, above, spot[3] if len(spot) > 3 else None)]
        for step in (-1, 1):                    # the pictures before and after, while it is there
            k = 1
            while True:
                got = rec["read"].get(iso(t + step * k * cad))
                near = [s for s in (got or []) if haversine_km(lat, lon, s[0], s[1]) <= reach]
                if not near:
                    break
                best = max(near, key=lambda s: s[2])
                run.append((iso(t + step * k * cad), *best[:3], best[3] if len(best) > 3 else None))
                k += 1
        med = statistics.median(r[3] for r in run)
        warms = [r[4] for r in run if r[4] is not None]
        w105 = statistics.median(warms) if warms else None
        if cfg.get("_replay") and len(run) >= 3:
            log.info("   steady? %.4f,%.4f at %s: %d pictures, above %.0f (median), warmer at 10.5 micron by %s",
                     lat, lon, iso(t), len(run), med, "?" if w105 is None else f"{w105:.0f}")
        if len(run) < int(P["min_pictures"]) or med < float(P["min_median"]):
            continue
        if P.get("max_warm105") is not None and w105 is not None and w105 > float(P["max_warm105"]):
            continue                      # warm land in the sun, not a fire
        if _usual(lat, lon, P, geo):
            continue
        for when, la, lo, ab, _w in sorted(run):
            key = f"{when}|{round(la, 2)}|{round(lo, 2)}"
            if key in rec["told"]:
                continue
            rec["told"].append(key)
            rows.append({"t": when, "lat": la, "lon": lo, "rise": ab, "steady": True,
                         "steady_pictures": len(run)})
    return rows
