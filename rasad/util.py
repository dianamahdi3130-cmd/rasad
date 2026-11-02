"""Shared helpers: time, geometry, plain-Arabic wording, files, HTTP."""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import os
import tempfile
from pathlib import Path

import requests

VERSION = "1.0"
UTC = dt.timezone.utc
log = logging.getLogger("rasad")


# ----------------------------------------------------------------- time
def utcnow():
    fake = os.environ.get("RASAD_NOW")  # only for tests and replays of a past night
    return parse_time(fake) if fake else dt.datetime.now(UTC)


def floor_time(t, minutes):
    t = t.astimezone(UTC).replace(second=0, microsecond=0)
    return t - dt.timedelta(minutes=t.minute % int(minutes))


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s):
    s = str(s).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    return (t.replace(tzinfo=UTC) if t.tzinfo is None else t).astimezone(UTC)


def local_text(t, cfg, with_date=True):
    """'2026-11-02 الساعة 01:10 (بغداد)' in the configured local time."""
    lt = cfg["local_time"]
    loc = t + dt.timedelta(hours=float(lt["offset_hours"]))
    clock = f"{loc:%H:%M}"
    return f"{loc:%Y-%m-%d} الساعة {clock} ({lt['name']})" if with_date else f"{clock} ({lt['name']})"


# ----------------------------------------------------------------- geometry
def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


DIRS_AR = ["شمال", "شمال شرق", "شرق", "جنوب شرق", "جنوب", "جنوب غرب", "غرب", "شمال غرب"]


def direction_ar(bearing):
    return DIRS_AR[int(((bearing % 360) + 22.5) // 45) % 8]


def km_text(d):
    if d < 1:
        return "أقل من كيلومتر"
    return f"{d:.1f} كم" if d < 10 else f"{d:.0f} كم"


def box_around(lat, lon, half_km):
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]  # west, south, east, north


def bbox_px(bbox, width):
    """Image size whose pixels are roughly square on the ground."""
    w, s, e, n = bbox
    wkm = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    hkm = (n - s) * 111.32
    return int(width), max(16, int(round(width * hkm / wkm)))


def px_to_latlon(bbox, W, H, col, row):
    w, s, e, n = bbox
    return n - (row + 0.5) / H * (n - s), w + (col + 0.5) / W * (e - w)


def latlon_to_px(bbox, W, H, lat, lon):
    w, s, e, n = bbox
    return (lon - w) / (e - w) * W - 0.5, (n - lat) / (n - s) * H - 0.5


def in_bbox(b, lat, lon):
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def bbox_intersects(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ----------------------------------------------------------------- plain wording
def size_word(frp):
    """Fire size in plain words from fire radiative power (MW)."""
    if frp is None:
        return None
    if frp < 10:
        return "صغير"
    if frp < 50:
        return "متوسط"
    if frp < 200:
        return "كبير"
    return "ضخم"


def esc(s):
    return html.escape(str(s), quote=False)


# ----------------------------------------------------------------- files & http
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":") if compact else None,
                  indent=None if compact else 1)
    os.replace(tmp, path)


def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = f"rasad/{VERSION} (fire monitoring; github)"
    return s
