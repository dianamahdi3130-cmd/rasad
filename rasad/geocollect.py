"""Bounded, resumable collection of official geostationary fire symbols.

Recent images and outage catch-up share the same request and wall-clock budget.
Progress is kept in the existing slot cache; unrequested or missing tiles stay
pending. Rotating tiles and sources prevents a slow first region from consuming
every monitoring cycle.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import datetime as dt
import time

from .sources import detect_geo_layer
from .util import iso, log
from .wms import latest_time


def _key(layer, tile, when):
    return f"{layer['id']}|{','.join(map(str, tile))}|{iso(when)}"


def _pending(slots, key, age, settle, late):
    previous = slots.get(key)
    return previous is None or not (late or previous >= settle or age < settle)


def collect(cfg, wms, state, now, tiles, availability, *, detect=detect_geo_layer, latest=latest_time):
    """Return ``(detections, source_status, number_of_images_requested)``.

    ``scan_budget_s`` includes source-clock discovery and recent images, unlike
    the historical catch-up budget. WMS.budget also caps in-flight HTTP calls;
    simple offline adapters without that context manager remain supported.
    """
    E = cfg["eumetview"]
    layers = [layer for layer in E["fire_layers"] if layer.get("enabled", True)]
    if not layers or not tiles:
        return [], {}, 0
    seconds = max(0.0, float(E.get("scan_budget_s", 90)))
    max_images = max(0, int(E.get("scan_max_images", 12)))
    workers = max(1, min(4, int(E.get("scan_workers", 4))))
    started = time.monotonic()
    deadline = started + seconds
    slots = state.setdefault("slots", {})
    settle = float(E.get("settle_minutes", 25))
    n_recent = max(1, int(E.get("lookback_slots", 2)))
    catchup = max(0.0, float(E.get("catchup_hours", 2)))
    catchup_seconds = max(0.0, float(E.get("catchup_budget_s", 75)))
    tile_start = int(state.get("geo_scan_tile_cursor", 0)) % len(tiles)
    tile_order = [(i, tiles[i]) for i in range(tile_start, len(tiles))]
    tile_order += [(i, tiles[i]) for i in range(tile_start)]
    layer_start = int(state.get("geo_scan_layer_cursor", 0)) % len(layers)
    layers = layers[layer_start:] + layers[:layer_start]
    # A source outage must not always leave the same later source undiscovered.
    state["geo_scan_layer_cursor"] = (layer_start + 1) % len(layers)
    w, s = min(tile[0] for tile in tiles), min(tile[1] for tile in tiles)
    e, n = max(tile[2] for tile in tiles), max(tile[3] for tile in tiles)
    cx, cy = (w + e) / 2, (s + n) / 2
    probe = [cx - 0.2, cy - 0.2, cx + 0.2, cy + 0.2]
    status = {layer["id"]: {"name": layer.get("label_ar", layer["id"]), "ok": False,
                             "latest": None, "checked": 0, "missing": 0,
                             "pending": 0, "pending_recent": 0, "scan_complete": False}
              for layer in layers}
    available = []
    detections, requested = [], 0
    budget_method = getattr(type(wms), "budget", None)
    context = budget_method(wms, seconds, timeout_s=float(E.get("scan_timeout_s", 20))) \
        if callable(budget_method) else nullcontext()

    def time_left():
        return time.monotonic() < deadline

    with context:
        for layer in layers:
            if not time_left() or not max_images:
                break
            cadence = int(layer["cadence_min"])
            try:
                newest = latest(wms, state, layer.get("clock_layer") or layer["name"], cadence, probe)
            except Exception as exc:  # an unavailable clock must not abort the other source
                log.warning("%s: source clock failed: %s", layer["id"], exc)
                continue
            fresh = newest is not None and dt.timedelta(0) <= now - newest <= dt.timedelta(hours=3)
            status[layer["id"]].update(ok=fresh, latest=iso(newest) if newest else None)
            if fresh:
                span = max(n_recent, int(round(catchup * 60 / cadence)))
                available.append((layer, newest, cadence, span))
            else:
                log.warning("%s: no recent data", layer["name"])

        def call(job):
            layer, tile_index, tile, when, age, key = job
            if not time_left():
                return False, None
            try:
                result = detect(wms, dict(layer, tile_px=layer.get("tile_px", E["tile_px"])), tile, when)
                return True, result
            except Exception as exc:
                log.warning("%s: tile request failed: %s", layer["id"], exc)
                return True, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for offset in range(max((row[3] for row in available), default=0)):
                late = offset >= n_recent
                if not time_left() or requested >= max_images:
                    break
                if late and time.monotonic() - started >= catchup_seconds:
                    break
                # All sources' newest tiles precede every older slot. Availability
                # probes are only sent when at least one tile still needs reading.
                phase = []
                for layer, newest, cadence, span in available:
                    if offset >= span:
                        continue
                    when = newest - dt.timedelta(minutes=cadence * offset)
                    age = (now - when).total_seconds() / 60
                    jobs = [(layer, index, tile, when, age, _key(layer, tile, when))
                            for index, tile in tile_order
                            if _pending(slots, _key(layer, tile, when), age, settle, late)]
                    if not jobs:
                        continue
                    if not time_left():
                        break
                    if offset and not availability.has(layer["name"], when):
                        continue
                    phase.append(jobs)
                # Interleave source requests for each tile, rather than spending
                # the entire cycle on the first layer in the configuration.
                jobs = [queue[index] for index in range(max(map(len, phase), default=0))
                        for queue in phase if index < len(queue)]
                for begin in range(0, len(jobs), workers):
                    if not time_left() or requested >= max_images:
                        break
                    batch = jobs[begin:begin + min(workers, max_images - requested)]
                    results = list(pool.map(call, batch))
                    for job, (attempted, result) in zip(batch, results):
                        if not attempted:
                            continue
                        layer, tile_index, tile, when, age, key = job
                        requested += 1
                        state["geo_scan_tile_cursor"] = (tile_index + 1) % len(tiles)
                        row = status[layer["id"]]
                        row["checked"] += 1
                        if result is None:
                            row["missing"] += 1
                        else:
                            detections.extend(result)
                            slots[key] = age

    # The backlog remains visible and is derived from successful cache writes,
    # so exhausting the budget never turns unrequested images into empty images.
    for layer, newest, cadence, span in available:
        row = status[layer["id"]]
        for offset in range(span):
            when = newest - dt.timedelta(minutes=cadence * offset)
            age = (now - when).total_seconds() / 60
            count = sum(_pending(slots, _key(layer, tile, when), age, settle, offset >= n_recent)
                        for tile in tiles)
            row["pending"] += count
            if offset < n_recent:
                row["pending_recent"] += count
        row["scan_complete"] = row["pending_recent"] == 0
    elapsed = max(0.0, time.monotonic() - started)
    exhausted = elapsed >= seconds or requested >= max_images
    for row in status.values():
        row["budget_exhausted"] = bool(exhausted and row["pending"])
    log.info("European satellites: %d images read, %d fire points, %.1f s, %d tiles pending",
             requested, len(detections), elapsed, sum(row["pending"] for row in status.values()))
    return detections, status, requested
