"""Following a posted fire at sea once the finders have lost it («ما زالت الحرارة ظاهرة»).

The case: Hormuz, 24-25 Nov 2026. A fire 22 km north of Khasab was posted at 00:10 Baghdad time and
seen by the sea finder until 03:30. The heat went on: faint at dawn, then clear in every
Meteosat-12 picture from 08:10 to 17:50, drifting with the strait's currents (1.5-2.5 km/h) 7-12 km
from its last sighting (docs/reviews/hormuz_dusk_20261125.md). The finders look for heat that is new
against the half hour before, so a fire that burns on is "hot before" to them. NASA's day pass made
a new post at 13:02, and the owner asked whether another ship had been hit.

So, after a posted sea fire goes quiet, each check reads the newest picture around where it was last
seen, and takes the warmest spot of open sea within the distance a burning hull can drift since
then. Three pictures in a row, each spot close to the one before, go out once as a reply under the
fire's post. The reply says what the pictures show and how far from the last sighting, never that it
is the same fire and never a cause. It never makes an incident, never changes a verdict, and never
decides whether anything else is posted.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from . import patterns
from .util import bearing_deg, box_around, direction_ar, floor_time, haversine_km, iso, log, parse_time

SETTINGS = {
    "enabled": False,       # on in config.yaml
    "post": True,
    "hours": 12.0,          # follow for this long after the last sighting (the fire's own or a followed one)
    "quiet_min": 30,        # start once the fire has not been seen for this long
    "drift_kmh": 3.0,       # how far a burning hull drifts: the strait's currents, 1.5-2.5 km/h measured
    "base_km": 3.0,
    "max_km": 20.0,
    "box_half_km": 22.0,    # the picture read around the last sighting
    "min_above": 4.0,       # levels of the picture's red above the sea around (12 km): the Hormuz fire
                            # was 4-12 by day and 4-6 at night; open sea elsewhere stayed under 3
    "pictures": 3,          # pictures in a row before the reply
    "step_km": 3.0,         # a spot further than this (plus the drift) from the one before starts again
    "coast_km": 2.0,        # islets and shores warm in the sun
    "flare_km": 1.5,        # a usual flare of NASA's long-term map this close is a platform, not the fire
    "max_fires": 3,         # fires followed in one check (one small picture each)
    "slots_per_run": 3,     # pictures caught up per fire in one check
    "budget_s": 12,
    "keep": 24,
}


def settings(cfg):
    return {**SETTINGS, **(cfg.get("follow") or {})}


def last_seen(inc):
    """(time, lat, lon) of the newest sighting: the fire's own or a followed one."""
    best = None
    for o in inc.get("observations") or []:
        try:
            t = parse_time(o["t"])
            row = (t, float(o["lat"]), float(o["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or row[0] > best[0]:
            best = row
    if best is None:
        try:
            best = (parse_time(inc["last"]), float(inc["lat"]), float(inc["lon"]))
        except (KeyError, TypeError, ValueError):
            return None
    track = (inc.get("follow") or {}).get("track") or []
    if track:
        t, lat, lon = parse_time(track[-1][0]), float(track[-1][1]), float(track[-1][2])
        if t > best[0]:
            best = (t, lat, lon)
    return best


def own_last(inc):
    try:
        return max(parse_time(o["t"]) for o in inc.get("observations") or [] if o.get("t"))
    except ValueError:
        return parse_time(inc["last"])


def wanted(inc, cfg, now):
    """A posted fire at sea, quiet for a while, not followed past the hours, not replied to yet."""
    F = settings(cfg)
    if not F.get("enabled") or inc.get("still_posted") or inc.get("recovered_historical"):
        return False
    if inc.get("status") != "alerted" or not inc.get("tg_msg") or inc.get("place") != "sea":
        return False
    seen = last_seen(inc)
    if seen is None:
        return False
    quiet = (now - own_last(inc)).total_seconds() / 60
    since = (now - seen[0]).total_seconds() / 3600
    return quiet >= float(F["quiet_min"]) and since <= float(F["hours"])


def _excluded(lat, lon, geo, F):
    """A platform or a usual flare: steady heat that is not the fire."""
    try:
        if geo is not None and getattr(geo, "platform_within", None) and geo.platform_within(lat, lon, 3.0):
            return True
    except Exception:  # noqa: BLE001 - a lookup that fails excludes nothing
        pass
    A = patterns.atlas()
    if A:
        if patterns.usual_flare_km(A, lat, lon, float(F["flare_km"]), 0.10) is not None:
            return True
        if patterns.spot_count(A, lat, lon, float(F["flare_km"])) >= 3:
            return True
    return False


def look(cfg, pics, inc, t, geo=None):
    """Read the picture of time t around the fire's last sighting. Returns a sighting {"t", "lat",
    "lon", "above", "km"}, {"t", "miss": True} when the picture shows nothing there, or None when the
    picture is not there (not published yet, or the sea could not be told from land)."""
    from .seawatch import box_size, local_mean, sea_grid

    F, C = settings(cfg), cfg["seawatch"]
    seen = last_seen(inc)
    if seen is None:
        return None
    t0, lat0, lon0 = seen
    km = float(C["km_per_px"])
    bbox = box_around(lat0, lon0, float(F["box_half_km"]))
    W, H = box_size({"bbox": bbox}, km)
    cur = pics.get(C["layer"], bbox, W, H, t)
    if cur is None:
        return None
    sea = sea_grid(bbox, W, H, float(F["coast_km"]), km)
    if sea is None:
        return None
    hours = max(0.0, (t - t0).total_seconds() / 3600)
    reach = min(float(F["max_km"]), float(F["base_km"]) + float(F["drift_kmh"]) * hours)
    w, s, e, n = bbox
    lats = n - (np.arange(H) + 0.5) / H * (n - s)
    lons = w + (np.arange(W) + 0.5) / W * (e - w)
    gl, go = np.meshgrid(lats, lons, indexing="ij")
    dist = np.hypot((gl - lat0) * 111.32, (go - lon0) * 111.32 * math.cos(math.radians(lat0)))
    hot = cur - local_mean(cur, max(4, round(12 / km)))
    b = np.where(np.isnan(hot) | ~sea | (dist > reach), -1e9, hot)
    k = max(3, round(3 / km))
    for _ in range(5):
        r, c = np.unravel_index(int(np.argmax(b)), b.shape)
        v = float(b[r, c])
        if v < float(F["min_above"]):
            break
        lat, lon = float(lats[r]), float(lons[c])
        if not _excluded(lat, lon, geo, F):
            return {"t": iso(t), "lat": round(lat, 4), "lon": round(lon, 4), "above": round(v, 1),
                    "km": round(haversine_km(lat0, lon0, lat, lon), 1)}
        b[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1] = -1e9
    return {"t": iso(t), "miss": True}


def note(inc, got, cfg):
    """Keep a look on the incident: the track of sightings and the streak of pictures in a row."""
    F = settings(cfg)
    rec = inc.setdefault("follow", {"track": [], "streak": 0})
    rec["looked"] = got["t"]
    if got.get("miss"):
        rec["streak"] = 0
        return rec
    track = rec["track"]
    if track and rec.get("streak"):
        pt, plat, plon = parse_time(track[-1][0]), track[-1][1], track[-1][2]
        gap = (parse_time(got["t"]) - pt).total_seconds() / 3600
        close = haversine_km(plat, plon, got["lat"], got["lon"]) <= float(F["step_km"]) + float(F["drift_kmh"]) * gap
        rec["streak"] = rec["streak"] + 1 if close and gap <= 0.5 else 1
    else:
        rec["streak"] = 1
    if not rec.get("from"):
        seen = last_seen(dict(inc, follow={}))
        if seen:
            rec["from"] = [iso(seen[0]), round(seen[1], 4), round(seen[2], 4)]
    track.append([got["t"], got["lat"], got["lon"], got["above"]])
    del track[:-int(F["keep"])]
    return rec


def run(cfg, wms, incidents, now, geo=None, pics=None):
    """Look at the newest pictures around the posted sea fires that went quiet."""
    from .seawatch import Pictures

    F, C = settings(cfg), cfg["seawatch"]
    if not F.get("enabled"):
        return 0
    cad = int(C["cadence_min"])
    latest = floor_time(now - dt.timedelta(minutes=int(C["delay_min"])), cad)
    fires = sorted((i for i in incidents if wanted(i, cfg, now)), key=lambda i: i["last"], reverse=True)
    pics = pics or Pictures(wms, attempts=1)
    looked = 0
    for inc in fires[:int(F["max_fires"])]:
        rec = inc.get("follow") or {}
        start = parse_time(rec["looked"]) + dt.timedelta(minutes=cad) if rec.get("looked") else \
            floor_time(own_last(inc), cad) + dt.timedelta(minutes=cad)
        slots = []
        t = max(start, latest - dt.timedelta(minutes=cad * (int(F["slots_per_run"]) - 1)))
        while t <= latest:
            slots.append(t)
            t += dt.timedelta(minutes=cad)
        for t in slots:
            got = look(cfg, pics, inc, t, geo)
            if got is None:
                break            # not published yet: the next check reads it
            note(inc, got, cfg)
            looked += 1
            if not got.get("miss"):
                log.info("following %s: heat %.1f above the sea at %s, %.1f km from the last sighting",
                         inc.get("id"), got["above"], got["t"], got["km"])
            if due(inc, cfg):
                break
    return looked


def due(inc, cfg):
    F = settings(cfg)
    rec = inc.get("follow") or {}
    return bool(F.get("enabled") and F.get("post") and not inc.get("still_posted")
                and int(rec.get("streak") or 0) >= int(F["pictures"]))


def _pictures(n):
    return "صورتين متتاليتين" if n == 2 else f"{n} صور متتالية" if n <= 10 else f"{n} صورة متتالية"


def _hhmm(t, cfg):
    lt = cfg["local_time"]
    return f"{t + dt.timedelta(hours=float(lt['offset_hours'])):%H:%M}"


def message(inc, cfg):
    """The reply under the fire's post: what the pictures show, where, and how far from the last
    sighting. Never that it is the same fire, never a cause."""
    rec = inc.get("follow") or {}
    track = rec.get("track") or []
    if not track:
        return ""
    t, lat, lon = parse_time(track[-1][0]), track[-1][1], track[-1][2]
    frm = rec.get("from") or []
    lines = ["🔥 <b>ما زالت الحرارة ظاهرة قرب موضع هذا الحريق</b>"]
    where = ""
    if len(frm) == 3:
        f_t, f_lat, f_lon = parse_time(frm[0]), frm[1], frm[2]
        d = haversine_km(f_lat, f_lon, lat, lon)
        since = _hhmm(f_t, cfg)
        if d < 1.5:
            where = f"، في الموضع الذي رُصد فيه آخر مرة عند {since} تقريباً"
        else:
            where = (f"، على بعد {d:.0f} كم {direction_ar(bearing_deg(f_lat, f_lon, lat, lon))} "
                     f"الموضع الذي رُصد فيه آخر مرة عند {since}")
    n = int(rec.get("streak") or len(track))
    lines.append(f"📍 في {_pictures(n)} للقمر الأوروبي حتى {_hhmm(t, cfg)} بتوقيت {cfg['local_time']['name']}"
                 f"{where}")
    lines.append("")
    lines.append(f"<code>{lat:.4f}, {lon:.4f}</code>")
    lines.append(f'<a href="https://www.google.com/maps?q={lat:.5f},{lon:.5f}&t=k">الخريطة</a>')
    lines.append("")
    lines.append("قد تكون النار نفسها وقد انجرفت مع التيار؛ صور الأقمار ترى الحرارة ولا ترى سببها.")
    return "\n".join(lines)
