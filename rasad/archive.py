"""The long-term record: every event worth remembering, kept after it leaves the live feed.

The live feed holds one week. The owner asked to search older fires by place, date and kind, so
each event worth keeping is written, slimmed down, into one small file per month:
  <state>/archive/2026-11.json   the record itself (travels with the memory)
  <site>/data/archive/…          the same files plus index.json, for the app to read
An event is replaced by its newer version while it is still live, and stays as it last was once
the engine forgets it. Nothing here is ever posted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .util import iso, load_json, log, save_json

KEEP = ("id", "kind", "first", "last", "lat", "lon", "headline", "where", "country", "place", "verdict",
        "confidence", "confidence_level", "category", "severity", "max_frp", "size", "sources_text",
        "regions", "region_name", "reason")
MIN_MW = 20.0


def worth(inc):
    """Kept: what was important, what was at least likely, and anything of some strength."""
    if not inc.get("headline") or inc.get("preliminary"):
        return False
    return (inc.get("priority") == "high" or inc.get("verdict") in ("مؤكد", "مرجّح")
            or (inc.get("max_frp") or 0) >= MIN_MW or bool(inc.get("alerted_at")))


def slim(inc):
    row = {k: inc.get(k) for k in KEEP}
    f = inc.get("facility") or {}
    row["facility"] = f.get("label") or None
    row["facility_km"] = f.get("km")
    row["alerted"] = bool(inc.get("alerted_at"))
    row["pictures"] = len(inc.get("geo_slots") or [])
    if inc.get("pattern"):   # the heat pattern's key (patterns.py), for searching the record later
        row["pattern"] = inc["pattern"].get("key")
    if isinstance(row.get("max_frp"), float):
        row["max_frp"] = round(row["max_frp"], 1)
    return row


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def update(cfg, incidents, now):
    """Bring the monthly files up to date with the incidents the engine still holds. Returns the
    months that changed. A failure here must never stop a check: the caller only logs it."""
    root = Path(cfg["_state_dir"]) / "archive"
    site = Path(cfg["_site_dir"]) / "data" / "archive"
    by_month = {}
    for inc in incidents:
        if worth(inc):
            by_month.setdefault(str(inc["first"])[:7], []).append(slim(inc))
    changed = []
    for month, rows in sorted(by_month.items()):
        path = root / f"{month}.json"
        stored = load_json(path, {"month": month, "items": []})
        known = {r["id"]: r for r in stored.get("items") or [] if isinstance(r, dict) and r.get("id")}
        before = _digest(known)
        for row in rows:
            known[row["id"]] = row
        if _digest(known) == before and path.exists():
            continue
        items = sorted(known.values(), key=lambda r: r.get("first") or "", reverse=True)
        save_json(path, {"month": month, "updated": iso(now), "items": items}, compact=True)
        changed.append(month)
    # the app's copies and the list of months (rebuilt when missing, e.g. on a fresh runner)
    months = sorted(p.stem for p in root.glob("????-??.json")) if root.exists() else []
    index = []
    for month in months:
        src, dst = root / f"{month}.json", site / f"{month}.json"
        if month in changed or not dst.exists() or dst.stat().st_size != src.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        data = load_json(src, {})
        index.append({"month": month, "count": len(data.get("items") or []), "updated": data.get("updated")})
    if months:
        wanted = {"generated": iso(now), "months": sorted(index, key=lambda r: r["month"], reverse=True)}
        current = load_json(site / "index.json", {})
        if current.get("months") != wanted["months"]:
            save_json(site / "index.json", wanted, compact=True)
    if changed:
        log.info("archive: %s updated", ", ".join(changed))
    return changed
