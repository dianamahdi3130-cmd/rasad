"""Match a connected set of pixels from one satellite pass as one acquisition.

Pixel order in a CSV must not decide which incident receives its measurements.
These groups describe acquisition footprints, not proof of separate physical fires.
"""
from __future__ import annotations

import math

from .brain import _footprint_radius, find_incident
from .util import haversine_km


def _position(det):
    try:
        lat, lon = float(det["lat"]), float(det["lon"])
        if math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


def _sort_key(det):
    position = _position(det) or (float("inf"), float("inf"))
    return (str(det.get("t", "")), str(det.get("src", "")), str(det.get("satellite", "")),
            *position, str(det.get("frp", "")), str(det.get("conf", "")))


def _within_radius(rows, maximum):
    lat = sum(float(row["lat"]) for row in rows) / len(rows)
    # Unwrap longitude relative to the first point, so a pass crossing the date
    # line is not incorrectly assigned a centroid near Greenwich.
    anchor = float(rows[0]["lon"])
    lon = anchor + sum((float(row["lon"]) - anchor + 180) % 360 - 180 for row in rows) / len(rows)
    lon = (lon + 180) % 360 - 180
    return all(haversine_km(lat, lon, float(row["lat"]), float(row["lon"])) <= maximum + 1e-9
               for row in rows)


def _clusters(rows, maximum):
    """Connected footprint components, with a bounded radius to stop long chains."""
    rows = sorted(rows, key=_sort_key)
    parent = list(range(len(rows)))
    members = {index: [row] for index, row in enumerate(rows)}

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    # Sorting by latitude keeps unrelated rows out of the exact distance check.
    latitude_limit = maximum / 110.0
    radii = [_footprint_radius(row) for row in rows]
    for i, first in enumerate(rows):
        lat, lon = float(first["lat"]), float(first["lon"])
        for j in range(i + 1, len(rows)):
            other = rows[j]
            if float(other["lat"]) - lat > latitude_limit:
                break
            reach = min(maximum, radii[i] + radii[j])
            if haversine_km(lat, lon, float(other["lat"]), float(other["lon"])) <= reach:
                a, b = root(i), root(j)
                if a != b and _within_radius(members[a] + members[b], maximum):
                    parent[b] = a
                    members[a].extend(members.pop(b))
    # Each union has a real footprint-overlap edge and stays within the bounded
    # radius. Long chains therefore split without losing or fabricating pixels.
    return [sorted(component, key=_sort_key) for component in members.values()]


def groups(dets, merge_km=6):
    """Return deterministic groups; non-polar observations remain singletons.

    Only overlapping pixels from exactly the same source, platform and time may
    share an acquisition. Distinct timestamps never confirm each other here.
    """
    maximum = max(0.001, float(merge_km))
    passes, result = {}, []
    for det in dets:
        if det.get("kind") != "polar" or _position(det) is None:
            result.append([det])
            continue
        key = (str(det.get("src", "")), str(det.get("satellite", "")), str(det.get("t", "")))
        passes.setdefault(key, []).append(det)
    for key in sorted(passes):
        result.extend(_clusters(passes[key], maximum))
    return sorted(result, key=lambda group: _sort_key(group[0]))


def find_group(incidents, group, merge_km, merge_hours, rules=None):
    """Make one association decision and propagate it to every member pixel.

    Callers must add all eligible members to this returned incident (or create
    one from ``group[0]``) without matching later members again independently.
    """
    if not group:
        return None
    for det in group:
        det.pop("_association", None)
    representative = dict(group[0])
    match = find_incident(incidents, representative, merge_km, merge_hours, rules=rules,
                          acquisition=group if representative.get("kind") == "polar" else None)
    association = representative.get("_association")
    if match is None and association:
        for det in group:
            det["_association"] = dict(association)
    return match
