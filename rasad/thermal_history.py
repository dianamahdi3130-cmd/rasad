"""Exclude persistent heat at its own location, rather than a whole nearby area.

Memory contains 0.02-degree cells, not exact historical point positions. Cell
centres are the available location estimate (about 1.5 km uncertainty in the
Gulf). The configured exclusion radius is measured from that centre without
adding an uncertainty buffer that would hide unrelated neighbouring fires.
"""
from __future__ import annotations

import datetime as dt
import math

from .brain import CELL
from .util import haversine_km


def _number(settings, name, default):
    try:
        value = settings.get(name, default)
        if isinstance(value, bool):
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def history_rules(settings):
    """JSON-safe effective rule settings used to invalidate completed scans."""
    return {"version": 1,
            "known_heat_km": max(0.0, _number(settings, "known_heat_km", 10.0)),
            "known_heat_exclusion_km": max(0.0, _number(settings, "known_heat_exclusion_km", 3.0)),
            "known_heat_min_days": max(1, int(_number(settings, "known_heat_min_days", 3)))}


def _nearby_cells(cells, lat, lon, radius):
    """Bound normal regional lookups; retain correct edge/polar behaviour."""
    latitude_span = radius / 110.0
    cosine = math.cos(math.radians(min(90.0, abs(lat) + latitude_span)))
    longitude_span = min(180.0, latitude_span / max(cosine, 1e-9))
    south, north = max(-90.0, lat - latitude_span), min(90.0, lat + latitude_span)
    west, east = lon - longitude_span, lon + longitude_span
    i0, i1 = math.floor(south / CELL) - 1, math.floor(north / CELL) + 1
    j0, j1 = math.floor(west / CELL) - 1, math.floor(east / CELL) + 1
    if west < -180 or east > 180 or (i1 - i0 + 1) * (j1 - j0 + 1) > 10000:
        # Exceptional dateline/polar or very wide configurations: scan existing
        # cells rather than making a huge grid of empty dictionary lookups.
        for key, history in cells.items():
            try:
                parts = key.split(",")
                if len(parts) != 2:
                    continue
                i, j = map(int, parts)
                if str(i) + "," + str(j) != key:
                    continue
                yield i, j, history
            except (AttributeError, TypeError, ValueError):
                continue
    else:
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                history = cells.get(f"{i},{j}")
                if history is not None:
                    yield i, j, history


def persistent_local_heat(mem, lat, lon, before_day, settings):
    """Whether one nearby cell has recurring heat on enough prior dates.

    ``before_day`` is exclusive. Night callers use their night-start date so
    observations on opposite sides of UTC midnight do not count as old heat.
    Dates cannot be combined across different cells to manufacture persistence.
    Unknown-power readings (zero in Memory) still record observed heat.
    """
    cells = getattr(mem, "cells", None)
    if not isinstance(cells, dict):
        return False
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            return False
        lat, lon = float(lat), float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            return False
        if isinstance(before_day, dt.datetime):
            before_day = before_day.date()
        elif not isinstance(before_day, dt.date):
            before_day = dt.date.fromisoformat(before_day)
    except (TypeError, ValueError, OverflowError):
        return False
    rules = history_rules(settings)
    radius = min(rules["known_heat_km"], rules["known_heat_exclusion_km"])
    if radius <= 0:
        return False
    minimum = rules["known_heat_min_days"]
    lower = before_day - dt.timedelta(days=30)
    for i, j, history in _nearby_cells(cells, lat, lon, radius):
        # Generated memory keys are floors. Negative cells must not be rounded
        # towards zero; the centre is always (index + 0.5) * CELL.
        if not -4500 <= i <= 4500 or not -9000 <= j <= 9000 or not isinstance(history, dict):
            continue
        cell_lat, cell_lon = min(90.0, (i + .5) * CELL), (j + .5) * CELL
        if haversine_km(lat, lon, cell_lat, cell_lon) > radius:
            continue
        days = set()
        for value, power in history.items():
            try:
                day = dt.date.fromisoformat(value)
                if isinstance(power, bool) or not math.isfinite(float(power)) or float(power) < 0:
                    continue
                if lower <= day < before_day:
                    days.add(day)
            except (TypeError, ValueError, OverflowError):
                continue
        if len(days) >= minimum:
            return True
    return False
