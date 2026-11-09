"""Bounded replay of official fire products near possibly missed facility surges.

Daily heat memory selects places to inspect; it is never returned as a detection.
Only a timestamp-validated official symbol with a measured FRP can become evidence.
Recovered observations are marked for historical publication without breaking-news alerts.
"""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import json
import math
import time
from contextlib import nullcontext

from .brain import CELL
from .sources import detect_geo_layer, fetch_frp
from .util import box_around, floor_time, haversine_km, iso, parse_time

VERSION = 1
OFFICIAL = frozenset({"mtg_frp", "iodc_fire"})


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def _targets(cfg, memory, geo, now, layers):
    """Explicit reviewed windows first, then unusual recent daily-memory cells."""
    R, M = cfg.get("recovery", {}), cfg["memory"]
    max_targets = max(1, min(24, int(R.get("max_targets", 6))))
    oldest = now - dt.timedelta(days=float(cfg["incidents"].get("keep_days", 7)))
    targets, invalid = [], 0
    for row in R.get("targets") or []:
        try:
            lat, lon = _number(row.get("lat")), _number(row.get("lon"))
            start, end = parse_time(row["start"]), parse_time(row["end"])
            layer = row.get("layer", "mtg_frp")
            if (lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180
                    or layer not in layers or end < start or end < oldest or start > now):
                invalid += 1
                continue
            # A reviewed historical window cannot cause an unlimited archive scan.
            start = max(start, oldest, end - dt.timedelta(hours=24))
            key = _key(["explicit", row.get("id"), lat, lon, iso(start), iso(end), layer])
            targets.append({"key": key, "lat": lat, "lon": lon, "start": start,
                            "end": min(end, now), "layer": layer, "explicit": True})
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            invalid += 1
        if len(targets) >= max_targets:
            break
    if not R.get("auto_targets", True) or memory is None or geo is None or len(targets) >= max_targets:
        return targets, invalid

    lo = now - dt.timedelta(hours=max(1, min(48, float(R.get("auto_hours", 24)))))
    minimum = float(M.get("unusual_add_mw", 25))
    ratio = float(M.get("unusual_ratio", 5))
    reach = 3  # the same coarse Meteosat neighbourhood used by live judgement
    candidates = []
    for cell, days in memory.cells.items():
        try:
            ci, cj = map(int, cell.split(","))
            lat, lon = (ci + 0.5) * CELL, (cj + 0.5) * CELL
            if abs(lat) > 90 or abs(lon) > 180:
                continue
            for day, value in days.items():
                day_start = parse_time(day + "T00:00:00Z")
                day_end = day_start + dt.timedelta(days=1) - dt.timedelta(seconds=1)
                peak = _number(value)
                if day_end < lo or day_start > now or peak is None or peak < minimum:
                    continue
                count, typical = memory.profile(lat, lon, day, int(M.get("window_days", 30)), reach)
                if count < 3 or typical is None or peak < max(ratio * typical, typical + minimum):
                    continue
                if not geo.facility_within(lat, lon, float(M.get("unusual_facility_km", 3))):
                    continue
                candidates.append((peak, day, cell, lat, lon, day_start, day_end))
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
    for peak, day, cell, lat, lon, start, end in sorted(candidates, reverse=True):
        # Adjacent memory cells commonly refer to the same coarse fire symbol.
        if any(t["start"].date() == start.date()
               and haversine_km(lat, lon, t["lat"], t["lon"]) < 4 for t in targets):
            continue
        layer = "mtg_frp" if "mtg_frp" in layers else next(iter(layers))
        targets.append({"key": _key(["auto", cell, day, layer]), "lat": lat, "lon": lon,
                        "start": max(start, lo), "end": min(end, now),
                        "layer": layer, "explicit": False})
        if len(targets) >= max_targets:
            break
    return targets, invalid


def _slots(target, layer, now):
    cadence = int(layer["cadence_min"])
    start = floor_time(target["start"], cadence)
    if start < target["start"]:
        start += dt.timedelta(minutes=cadence)
    end = floor_time(min(target["end"], now), cadence)
    return [start + dt.timedelta(minutes=cadence * k)
            for k in range(max(0, int((end - start).total_seconds() // (60 * cadence)) + 1))]


def collect(cfg, wms, state, memory, geo, now, detect=detect_geo_layer, fetch=fetch_frp):
    """Return ``(historical detections, status)`` without alerting or inventing FRP.

    Failed images and missing strength remain pending. A completed blank image is
    remembered, so a focused recovery progresses over subsequent short cycles.
    """
    R = cfg.get("recovery", {})
    status = {"enabled": bool(R.get("enabled", False)), "checked": 0, "missing": 0,
              "deferred": 0,
              "images_fetched": 0, "images_reused": 0,
              "detections": 0, "pending": 0, "targets": 0, "invalid_targets": 0,
              "historical": True, "latest": None}
    if not status["enabled"]:
        return [], status
    layers = {L["id"]: L for L in cfg["eumetview"]["fire_layers"]
              if L.get("enabled", True) and L["id"] in OFFICIAL}
    if not layers:
        return [], status
    targets, status["invalid_targets"] = _targets(cfg, memory, geo, now, layers)
    status["targets"] = len(targets)
    history = state.setdefault("facility_recovery", {"version": VERSION, "targets": {}})
    if history.get("version") != VERSION:
        history.clear()
        history.update(version=VERSION, targets={})
    saved = history.setdefault("targets", {})
    oldest = now - dt.timedelta(days=float(cfg["incidents"].get("keep_days", 7)))
    for key in list(saved):
        try:
            expired = parse_time(saved[key]["updated"]) < oldest
        except (TypeError, ValueError, KeyError):
            expired = True
        if expired:
            del saved[key]
    # Keep metadata bounded if many candidate cells appeared on one day.
    if len(saved) > 128:
        for key in sorted(saved, key=lambda k: saved[k].get("updated", ""))[:-128]:
            del saved[key]
    retry = dt.timedelta(minutes=max(1, float(R.get("retry_min", 10))))
    limit = max(0, min(12, int(R.get("max_images_per_run", 2))))
    feature_limit = max(1, min(16, int(R.get("max_features_per_image", 6))))
    deadline = time.monotonic() + max(0, float(R.get("budget_s", 30)))
    jobs = []
    for target in targets:
        progress = saved.setdefault(target["key"], {"done": [], "retry": {}, "features": {}, "updated": iso(now)})
        done = set(progress.setdefault("done", []))
        attempts = progress.setdefault("retry", {})
        progress.setdefault("features", {})
        progress.setdefault("feature_attempts", {})
        progress.setdefault("images", {})
        pending = [t for t in _slots(target, layers[target["layer"]], now) if iso(t) not in done]
        status["pending"] += len(pending)
        for t in pending:
            last = attempts.get(iso(t))
            if last:
                try:
                    if now - parse_time(last) < retry:
                        continue
                except (TypeError, ValueError):
                    pass
            jobs.append((target, progress, t))
    # Explicit reviewed windows complete first. Automatic windows rotate through
    # their oldest remaining slots, with no full-day global image download.
    out = []
    budget_method = getattr(type(wms), "budget", None)
    context = wms.budget(max(0, deadline - time.monotonic()), timeout_s=20) if callable(budget_method) else nullcontext()
    with context:
        for target, progress, when in jobs:
            if status["checked"] >= limit or time.monotonic() >= deadline:
                break
            L = dict(layers[target["layer"]], tile_px=min(512, max(32, int(R.get("tile_px", 512)))))
            half = max(2, min(24, float(R.get("half_size_km", 8))))
            tile = box_around(target["lat"], target["lon"], half)
            status["checked"] += 1
            progress["updated"] = iso(now)
            progress["retry"][iso(when)] = iso(now)
            slot = iso(when)
            images = progress["images"]

            def valid_symbol(det):
                try:
                    if (not isinstance(det, dict) or det.get("src") != L["id"]
                            or abs((parse_time(det["t"]) - when).total_seconds()) > 120):
                        return False
                    fi = det.get("original_fi", det.get("_fi"))
                    # Real query metadata includes its own time. It must not
                    # silently ask for another slot while retaining this label.
                    return not (isinstance(fi, (list, tuple)) and len(fi) == 7
                                and abs((parse_time(fi[-1]) - when).total_seconds()) > 120)
                except (KeyError, TypeError, ValueError, OverflowError):
                    return False

            cached = images.get(slot)
            if (isinstance(cached, dict) and cached.get("verified_t") == slot
                    and isinstance(cached.get("symbols"), list)
                    and all(valid_symbol(d) for d in cached["symbols"])):
                # Only metadata is stored, not the PNG. A slow verified image
                # must not consume the next cycle again before its FRP query.
                nearby = copy.deepcopy(cached["symbols"])
                nearby = [d for d in nearby if haversine_km(
                    d["lat"], d["lon"], target["lat"], target["lon"]) <= half]
                for det in nearby:
                    fi = det.pop("original_fi", None)
                    if fi is not None:
                        det["_fi"] = fi
                status["images_reused"] += 1
            else:
                images.pop(slot, None)
                status["images_fetched"] += 1
                detections = detect(wms, L, tile, when)
                if detections is None or not all(valid_symbol(d) for d in detections):
                    status["missing"] += 1
                    continue  # an absent or substituted image can never become a cached blank
                # A small target window can contain neighbouring flares. Keep
                # their original feature-info coordinates for subsequent passes.
                nearby = [d for d in detections if haversine_km(
                    d["lat"], d["lon"], target["lat"], target["lon"]) <= half]
                nearby.sort(key=lambda d: haversine_km(
                    d["lat"], d["lon"], target["lat"], target["lon"]))
                symbols = copy.deepcopy(nearby)
                for det in symbols:
                    det["original_fi"] = det.pop("_fi", None)
                images[slot] = {"verified_t": slot, "symbols": symbols}
            # A small target window can contain neighbouring flares. Bound
            # strength requests and inspect the nearest symbols first.
            features = progress["features"]
            checked_features = set(features.get(slot, []))

            def feature_key(det):
                return f"{det.get('src')}|{det.get('t')}|{det.get('lat')}|{det.get('lon')}"

            pending_features = [d for d in nearby if feature_key(d) not in checked_features]
            feature_attempts = progress["feature_attempts"].setdefault(slot, {})
            # A symbol whose FRP is repeatedly unavailable must not monopolize
            # every limited pass ahead of neighbouring, as-yet-unchecked ones.
            pending_features.sort(key=lambda d: feature_attempts.get(feature_key(d), ""))
            complete = len(pending_features) <= feature_limit
            missing_evidence = False
            for d in pending_features[:feature_limit]:
                if time.monotonic() >= deadline:
                    complete = False
                    break
                d = dict(d)
                feature_attempts[feature_key(d)] = iso(now)
                if d.get("frp") is None:
                    fetch(wms, d)
                power = _number(d.get("frp"))
                if power is None or power < 0:
                    complete = False
                    missing_evidence = True
                    continue
                # Official detector validates actual timestamps; repeat the
                # check here so injected/reused collectors cannot label a substitute.
                try:
                    if abs((parse_time(d["t"]) - when).total_seconds()) > 120:
                        complete = False
                        missing_evidence = True
                        continue
                except (KeyError, TypeError, ValueError):
                    complete = False
                    missing_evidence = True
                    continue
                d.pop("_fi", None)
                d.update(frp=power, _recovered=True, _no_alert=True)
                out.append(d)
                checked_features.add(feature_key(d))
                status["latest"] = max(status["latest"] or d["t"], d["t"])
            if complete:
                progress["done"] = sorted(set(progress["done"]) | {iso(when)})
                progress["retry"].pop(iso(when), None)
                features.pop(slot, None)
                progress["feature_attempts"].pop(slot, None)
                images.pop(slot, None)
                status["pending"] -= 1
            else:
                # A busy image may contain more symbols than this short pass
                # can validate. Resume with the remaining symbols next time;
                # reaching the feature cap never makes an image "complete".
                features[slot] = sorted(checked_features)
                status["missing"] += int(missing_evidence)
                status["deferred"] += int(not missing_evidence)
                if not missing_evidence:
                    # Work deferred solely by this cycle's cap can resume on
                    # the next cycle; actual unavailable evidence keeps backoff.
                    progress["retry"].pop(slot, None)
    uniq = {}
    for det in out:
        uniq[(det["src"], det["t"], det["lat"], det["lon"])] = det
    status["detections"] = len(uniq)
    return sorted(uniq.values(), key=lambda d: d["t"]), status
