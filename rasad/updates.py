"""Measured fire strength and bounded intensity-update eligibility."""
from __future__ import annotations

import datetime as dt
import math

from .util import iso, parse_time


def _reading(when, value):
    try:
        if value is None or isinstance(value, bool):
            return None
        mw = float(value)
        if not math.isfinite(mw) or mw < 0:
            return None
        return parse_time(when), mw
    except (TypeError, ValueError, OverflowError):
        return None


def _readings(inc):
    for row in inc.get("frp_series") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            reading = _reading(row[0], row[1])
            if reading is not None:
                yield reading
    for row in inc.get("polar") or []:
        if isinstance(row, dict):
            reading = _reading(row.get("t"), row.get("frp"))
            if reading is not None:
                yield reading


def peak_reading(inc):
    """The strongest finite measurement; use its latest time when peaks are tied."""
    reading = max(_readings(inc), key=lambda r: (r[1], r[0]), default=None)
    return (iso(reading[0]), reading[1]) if reading is not None else None


def latest_reading(inc):
    """The newest finite measurement; use the strongest value at the same instant."""
    reading = max(_readings(inc), key=lambda r: (r[0], r[1]), default=None)
    return (iso(reading[0]), reading[1]) if reading is not None else None


def intensity_update_due(inc, cfg, now):
    """A substantial peak since the last successful alert, after its cooldown.

    An absent baseline is a legacy incident requiring explicit migration; a stored None
    means the earlier alert had no strength measurement. Updates have their own bounded
    age window because late measurements update an existing post, not announce a new fire.
    This function never mutates state.
    """
    if inc.get("status") != "alerted" or "alerted_max_frp" not in inc:
        return False
    peak = peak_reading(inc)
    if peak is None:
        return False
    alerts = cfg["alerts"]
    try:
        now = parse_time(now)
        peak_age = now - parse_time(peak[0])
        last_post = parse_time(inc.get("alert_updated_at") or inc.get("alerted_at"))
    except (TypeError, ValueError, OverflowError):
        return False
    if not dt.timedelta(0) <= peak_age <= dt.timedelta(hours=float(alerts.get("intensity_max_age_hours", 24))):
        return False
    if now - last_post < dt.timedelta(minutes=float(alerts.get("intensity_interval_min", 30))):
        return False
    added = float(alerts.get("intensity_add_mw", 1))
    baseline = inc["alerted_max_frp"]
    if baseline is None:
        return peak[1] >= added
    previous = _reading(peak[0], baseline)
    if previous is None:
        return False
    baseline = previous[1]
    return (peak[1] > baseline and peak[1] >= baseline * float(alerts.get("intensity_ratio", 1.5))
            and peak[1] - baseline >= added)
