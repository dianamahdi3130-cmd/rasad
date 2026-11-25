"""One fire at sea, one marker (the app only): which records are the same heat, which may be, which are not.

Display only. It reads the stored records and adds two fields to what the app receives ("chain" and
"path"); it never changes an incident, a verdict or a post. The case: one burning ship off Khasab was
posted six times in two days, and the app showed four markers for it; the owner asked "why four
fires?" (docs/reviews/hormuz_one_source_20261125.md, docs/plans/sea_fire_identity.md).

What is said, and why:
- the same fire: its heat was seen from the older record to this one without a break (a sighting of
  the older record at most an hour before, within the streak a source is drawn as), or both were seen in
  the same picture within that streak;
- another fire: both in the same picture and further apart, or further than a burning hull drifts
  in the time between. Nothing is written: it stays its own marker;
- not settled: after a longer gap, within drifting distance. Written as such, never decided.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .util import haversine_km, iso, log, parse_time

SETTINGS = {"enabled": True, "same_km": 5.0, "same_minutes": 60, "picture_minutes": 10, "base_km": 5.0,
            "drift_kmh": 3.0, "max_km": 20.0, "max_hours": 12.0, "path_points": 160,
            # the channel (phase 2 of docs/plans/sea_fire_identity.md). "thread": decide and record at each
            # first alert at sea; "thread_post": act on it. False = silent: nothing is posted differently.
            "thread": True, "thread_post": False, "run_pictures": 3, "apart_km": 2.5}
REVIEWS = Path(__file__).resolve().parent / "reviews"
_reviewed = None


def settings(cfg):
    return dict(SETTINGS, **(cfg.get("chains") or {}))


def reviewed_tracks():
    """Tracks of past events read by hand from the real pictures (rasad/reviews/sea_track_*.json):
    {incident id: [[time, lat, lon], ...]}. They only draw a past path in the app."""
    global _reviewed
    if _reviewed is None:
        _reviewed = {}
        for path in sorted(REVIEWS.glob("sea_track_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                _reviewed.setdefault(str(data["incident"]), []).extend(data["rows"])
            except Exception as e:  # noqa: BLE001 - a damaged review file draws nothing
                log.warning("reviewed track %s: %s", path.name, e)
    return _reviewed


def sightings(inc):
    """[(time, lat, lon)] of a record, oldest first: its own sightings, where its heat was followed
    to (follow.py), and a reviewed track of it."""
    rows = []
    for o in inc.get("observations") or []:
        rows.append((o.get("t"), o.get("lat"), o.get("lon")))
    rows += [(r[0], r[1], r[2]) for r in (inc.get("follow") or {}).get("track") or [] if len(r) >= 3]
    rows += [(r[0], r[1], r[2]) for r in reviewed_tracks().get(str(inc.get("id")), []) if len(r) >= 3]
    out = []
    for t, lat, lon in rows:
        try:
            out.append((parse_time(t), float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def relation(older, t0, lat, lon, P):
    """How a sighting at t0 stands to an older record's sightings: ("same" | "maybe" | "other" | None,
    hours since the older record was last seen, km from it)."""
    near = [s for s in older if abs((s[0] - t0).total_seconds()) <= float(P["picture_minutes"]) * 60]
    if near:
        km = min(haversine_km(lat, lon, s[1], s[2]) for s in near)
        return ("same" if km <= float(P["same_km"]) else "other"), 0.0, km
    before = [s for s in older if s[0] < t0]
    if not before:
        return None, None, None
    last = before[-1]
    gap = (t0 - last[0]).total_seconds() / 3600
    km = haversine_km(lat, lon, last[1], last[2])
    if gap <= float(P["same_minutes"]) / 60 and km <= float(P["same_km"]):
        return "same", gap, km
    if gap <= float(P["max_hours"]) and km <= min(float(P["max_km"]), float(P["base_km"]) + float(P["drift_kmh"]) * gap):
        return "maybe", gap, km
    return None, gap, km


def _hours(h):
    from .brain import hours_ar
    return hours_ar(h)


def annotate(items, incidents, cfg):
    """Write "chain" and "path" on the app's items of fires at sea. Returns how many were linked."""
    P = settings(cfg)
    if not P.get("enabled"):
        return 0
    by_id = {it.get("id"): it for it in items if isinstance(it, dict)}
    sea = []
    for inc in incidents:
        if inc.get("place") == "sea" and inc.get("id") in by_id and inc.get("first"):
            rows = sightings(inc)
            if rows:
                sea.append((rows[0][0], inc, rows))
    sea.sort(key=lambda r: (r[0], r[1]["id"]))
    same_prev, maybe = {}, {}
    for k, (t0, inc, rows) in enumerate(sea):
        best = None
        for _, older, older_rows in sea[:k]:
            kind, gap, km = relation(older_rows, t0, rows[0][1], rows[0][2], P)
            if kind in ("same", "maybe"):
                rank = (0 if kind == "same" else 1, gap, km)
                if best is None or rank < best[0]:
                    best = (rank, kind, older, gap, km)
        if best is None:
            continue
        _, kind, older, gap, km = best
        if kind == "same":
            same_prev[inc["id"]] = older["id"]
        else:
            maybe[inc["id"]] = {"prev": older["id"], "gap_h": round(gap, 1), "km": round(km, 1),
                                "text": f"ظهر بعد انقطاع {_hours(gap)} على بعد {max(1, round(km))} كم من آخر موضع لحريق "
                                        "سابق؛ قد يكون الحريق نفسه وقد يكون حريقاً آخر"}

    def root(i):
        seen = set()
        while i in same_prev and i not in seen:
            seen.add(i)
            i = same_prev[i]
        return i
    members = {}
    for _, inc, _ in sea:
        members.setdefault(root(inc["id"]), []).append(inc["id"])
    first_of = {inc["id"]: iso(t0) for t0, inc, _ in sea}
    linked = 0
    for _, inc, rows in sea:
        item, r = by_id[inc["id"]], root(inc["id"])
        chain = {}
        if len(members[r]) > 1:
            chain.update(root=r, n=len(members[r]), since=first_of[r])
        if inc["id"] in maybe:
            chain["maybe"] = maybe[inc["id"]]
        if chain:
            item["chain"] = chain
            linked += 1
        path, seen = [], set()
        for t, lat, lon in rows:                       # one point a picture
            slot = int(t.timestamp()) // 600
            if slot not in seen:
                seen.add(slot)
                path.append([int(t.timestamp()), round(lat, 4), round(lon, 4)])
        step = max(1, -(-len(path) // int(P["path_points"])))
        path = path[::step] + ([path[-1]] if (len(path) - 1) % step else [])
        if chain or (len(path) >= 2 and haversine_km(path[0][1], path[0][2], path[-1][1], path[-1][2]) >= 1.0):
            item["path"] = path
    return linked


# ---------------------------------------------------------------- the channel (silent for now)
def decide(inc, incidents, cfg):
    """What the channel should do with a fire at sea that is about to get its first post: a reply under
    an older posted fire (its heat was seen in at least `run_pictures` pictures of the hour before, the
    newest of them within `same_km`: one sighting never proves it), or a post of its own, said as
    not settled when it lies within drifting distance of an older fire's last place. An older fire
    seen elsewhere in the same picture is another fire beyond `same_km`; between `apart_km` and
    `same_km` the two peaks may be one streak or two ships: not settled, and never a reply.
    Returns {"as": "reply" | "post", "link": "same" | "maybe" | None, ...}; None on land.
    While `thread_post` is false this is only recorded (engine: state["thread_log"])."""
    P = settings(cfg)
    if not (P.get("enabled") and P.get("thread")) or inc.get("place") != "sea":
        return None
    rows = sightings(inc)
    if not rows:
        return None
    t0, lat, lon = rows[0]
    picture = float(P["picture_minutes"]) * 60
    best, two_peaks = None, False
    for older in incidents:
        if older is inc or older.get("id") == inc.get("id") or older.get("place") != "sea" \
                or older.get("status") != "alerted" or not older.get("tg_msg") or older.get("recovered_historical"):
            continue
        # the older fire's own sightings in this very picture: a second peak beside it is never «the same»
        now_km = [haversine_km(lat, lon, s[1], s[2]) for s in sightings({"observations": older.get("observations")})
                  if abs((s[0] - t0).total_seconds()) <= picture]
        seen = [s for s in sightings(older) if s[0] <= t0 + dt.timedelta(seconds=picture)]
        if now_km and min(now_km) > float(P["apart_km"]):
            if min(now_km) > float(P["same_km"]):
                continue                   # it burns elsewhere in this very picture: two fires
            two_peaks = True               # two peaks within the length of one streak: not settled
            nearest = min((s for s in seen if abs((s[0] - t0).total_seconds()) <= picture),
                          key=lambda s: haversine_km(lat, lon, s[1], s[2]))
            kind, gap, km, last = "maybe", 0.0, min(now_km), nearest
        else:
            if not seen:
                continue
            last = seen[-1]
            gap = max(0.0, (t0 - last[0]).total_seconds() / 3600)
            km = haversine_km(lat, lon, last[1], last[2])
            run = [s for s in seen if (t0 - s[0]).total_seconds() <= float(P["same_minutes"]) * 60]
            if len(run) >= int(P["run_pictures"]) and km <= float(P["same_km"]):
                kind = "same"
            elif gap <= float(P["max_hours"]) and km <= min(float(P["max_km"]), float(P["base_km"]) + float(P["drift_kmh"]) * gap):
                kind = "maybe"
            else:
                continue
        rank = (0 if kind == "same" else 1, gap, km)
        if best is None or rank < best[0]:
            best = (rank, kind, older, gap, km, last)
    if best is None:
        return {"as": "post", "link": None}
    _, kind, older, gap, km, last = best
    if kind == "same" and two_peaks:
        kind = "maybe"                     # the picture shows this spot beside another posted fire's spot
    out = {"as": "reply" if kind == "same" else "post", "link": kind, "under": older["id"], "tg": older.get("tg_msg"),
           "gap_h": round(gap, 1), "km": round(km, 1), "from": [iso(last[0]), round(last[1], 4), round(last[2], 4)]}
    if kind == "maybe":
        seen_how = "ظهر في الصورة نفسها" if gap < 0.2 else f"ظهر بعد انقطاع {_hours(gap)}"
        out["text"] = (f"غير محسوم: {seen_how} على بعد {max(1, round(km))} كم من {'موضع' if gap < 0.2 else 'آخر موضع ل'}"
                       f"{' ' if gap < 0.2 else ''}حريق في البحر نُشر سابقاً؛ قد يكون الحريق نفسه وقد يكون حريقاً آخر")
    return out


def reply_message(inc, decision, cfg):
    """The reply under the older fire's post when its heat is seen again («same»): where it is now and
    who saw it. It says the heat was followed here, never a cause."""
    from .brain import FAMILY_AR, SOURCE_TG, mw_ar
    from .util import bearing_deg, direction_ar
    lt = cfg["local_time"]
    rows = sightings(inc)
    t0, lat, lon = rows[0]
    when = f"{t0 + dt.timedelta(hours=float(lt['offset_hours'])):%H:%M} بتوقيت {lt['name']}"
    lines = ["🔥 <b>ما زالت النار مشتعلة</b>"]
    frm = decision.get("from") or []
    if len(frm) == 3 and decision.get("km", 0) >= 1.5:
        lines.append(f"📍 رُصدت عند {when} على بعد {decision['km']:.0f} كم "
                     f"{direction_ar(bearing_deg(frm[1], frm[2], lat, lon))} موضعها السابق")
    else:
        lines.append(f"📍 رُصدت عند {when} قرب موضعها السابق")
    names = []
    for fam in inc.get("families") or []:
        n = SOURCE_TG.get(fam, FAMILY_AR.get(fam, fam))
        if n not in names:
            names.append(n)
    if names:
        lines.append(f"🛰 <b>رصدها:</b> {' و'.join(names)}")
    try:
        if inc.get("max_frp"):
            lines.append(f"🔥 <b>الشدة:</b> {mw_ar(float(inc['max_frp']))}")
    except (TypeError, ValueError):
        pass
    lines += ["", f"<code>{lat:.4f}, {lon:.4f}</code>", "",
              "الحرارة توبعت في الصور حتى هذا الموضع؛ صور الأقمار ترى الحرارة ولا ترى سببها."]
    return "\n".join(lines)
