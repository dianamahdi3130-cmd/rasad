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
            "drift_kmh": 3.0, "max_km": 20.0, "max_hours": 12.0, "path_points": 160}
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
