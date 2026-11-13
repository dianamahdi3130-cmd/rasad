"""App-only previews of accepted night spots awaiting a supported incident.

These records are ephemeral views of the finder's existing candidate state.
They never enter incident association, heat memory, media jobs or Telegram.
"""
from __future__ import annotations

import datetime as dt
import math

from .util import haversine_km, in_bbox, iso, local_text, parse_time, sun_elevation

SOURCE = "mtg_spot"
LIMIT = 25


def _position(row):
    try:
        if any(isinstance(row[key], bool) for key in ("lat", "lon")):
            return None
        lat, lon = float(row["lat"]), float(row["lon"])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _represented(lat, lon, when, incidents):
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        observations = incident.get("observations") or []
        for row in observations:
            if not isinstance(row, dict):
                continue
            try:
                pos = _position(row)
                if (row.get("src") == SOURCE and parse_time(row["t"]) == when and pos
                        and haversine_km(lat, lon, *pos) <= 3):
                    return True
            except (KeyError, TypeError, ValueError):
                continue
        # Only source-exclusive legacy records can identify old geo slots as
        # night observations. A nearby different source or time is not a match.
        if not observations and set(incident.get("srcs") or []) == {SOURCE}:
            pos = _position(incident)
            if pos and haversine_km(lat, lon, *pos) <= 3:
                for value in incident.get("own_night_slots") or incident.get("geo_slots") or []:
                    try:
                        if parse_time(value) == when:
                            return True
                    except (TypeError, ValueError):
                        continue
    return False


def _usual(state, lat, lon, when, threshold):
    night = (when - dt.timedelta(hours=12)).date()
    ci, cj = math.floor(lat / .05), math.floor(lon / .05)
    history = state.get("sea_sparkle") or {}
    if not isinstance(history, dict):
        history = {}
    before = set()
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            values = history.get(f"{ci + di},{cj + dj}", [])
            if not isinstance(values, (list, tuple)):
                continue
            for value in values:
                try:
                    day = dt.date.fromisoformat(value)
                    if day < night:
                        before.add(day)
                except (TypeError, ValueError):
                    continue
    return len(before) >= threshold


def public_candidates(cfg, state, incidents, now):
    """Return at most 25 fresh, unrepresented night previews without mutations."""
    settings = cfg.get("seawatch", {})
    if not settings.get("enabled", False):
        return []
    lifetime = dt.timedelta(hours=max(0, float(settings.get("catchup_hours", 3))) + 1)
    threshold = max(1, int(settings.get("sparkle_min_days", 3)))
    rows = []
    seen = set()
    for candidate in state.get("sea_candidates") or []:
        try:
            if not isinstance(candidate, dict) or candidate.get("src") != SOURCE:
                continue
            pos = _position(candidate)
            when = parse_time(candidate["t"])
            rise = candidate["rise"]
            if (not pos or isinstance(rise, bool) or not math.isfinite(float(rise))
                    or float(rise) <= 0 or not now - lifetime <= when <= now):
                continue
            lat, lon = pos
            if sun_elevation(lat, lon, when) > float(settings.get("sun_max_elev", -6)):
                continue
            regions = [r for r in cfg.get("regions", [])
                       if r.get("enabled", True) and in_bbox(r["bbox"], lat, lon)]
            if not regions or _usual(state, lat, lon, when, threshold):
                continue
            if _represented(lat, lon, when, incidents):
                continue
            stamp = iso(when)
            key = stamp, round(lat, 4), round(lon, 4)
            if key in seen:
                continue
            seen.add(key)
            regions.sort(key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
            region = regions[0].get("name", "")
            near = f" قرب {region}" if region else ""
            note = "رصد حراري أولي قيد التحقق؛ لا يتوفر فيديو لهذه البقعة حالياً"
            rows.append({
                "id": f"candidate-{stamp[:16].replace('-', '').replace(':', '').replace('T', '-')}_{lat:.4f}_{lon:.4f}",
                "kind": "thermal_candidate", "preliminary": True,
                "headline": "بقعة حرارية أولية في البحر" + near,
                "where": "في البحر" + near, "place": "sea", "country": "", "facility": None,
                "first": stamp, "last": stamp, "lat": lat, "lon": lon, "best": "geo",
                "start_text": local_text(when, cfg), "last_text": local_text(when, cfg),
                "verdict": "يحتاج تأكيداً", "priority": "high", "size": None,
                "reason": "إشارة حرارية أولية ظهرت ليلاً؛ ننتظر رصداً إضافياً لتقييمها",
                "max_frp": None, "frp_series": None, "clip": None, "snap": None,
                "clip_note": note, "clip_status_text": note,
                "sources_text": "أوروبي — رصد حراري أولي",
                "regions": [r["id"] for r in regions], "region_name": region,
            })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    rows.sort(key=lambda row: (row["last"], row["id"]), reverse=True)
    return rows[:LIMIT]
