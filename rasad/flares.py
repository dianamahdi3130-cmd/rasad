"""A plant's flare burning busier than usual: a silent marker for the app.

Gas flares at oil and gas plants are seen most nights and are not news. Sometimes a flare burns
far more often and clearly stronger than usual for hours (11-12 Nov 2026 at Yanbu and Shedgum:
Meteosat-12's fire symbol in 25-33 of ~45 night pictures instead of 3-12, readings two to five
times the usual) while staying below the 'unusual heat' line that alerts. That can be ordinary
extra flaring or the aftermath of damage; the pictures cannot tell which.

This module only notices it, for the app: «نشاط حراري أعلى من المعتاد (تجريبي)». It is never an
incident, never posted, never part of a verdict, and never trains the usual-heat memory. Its
record over real cases is to be reviewed with the owner (docs/reviews/yanbu_shedgum_20261112.md).

Rule (checked against three real nights at both plants): in the last `window_h` hours at a plant
with a steady flare, Meteosat-12 shows its fire symbol in at least `min_pictures` pictures, and
at least `min_strong` readings are both `strong_ratio` times the usual and `strong_add_mw` above it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math

from .brain import bidi_safe
from .util import iso, parse_time

SRC = "mtg_frp"   # Meteosat-12's official fire symbols: one picture every 10 minutes, with strength
DEFAULTS = {"enabled": True, "window_h": 3, "min_pictures": 8, "min_strong": 3,
            "strong_ratio": 2.0, "strong_add_mw": 5.0, "site_km": 6.0,
            "new_after_h": 6, "show_h": 48}


def _km(a_lat, a_lon, b_lat, b_lon):
    return math.hypot((a_lat - b_lat) * 111.0, (a_lon - b_lon) * 111.0 * math.cos(math.radians(a_lat)))


def strong_line(typical, cfg=None):
    C = {**DEFAULTS, **(cfg or {})}
    return max(float(C["strong_ratio"]) * typical, typical + float(C["strong_add_mw"]))


def observe(state, det, typical, facility, cfg=None):
    """Note one Meteosat-12 fire symbol at a plant whose flare is steady (`typical` = its usual
    daily strongest heat in MW, `facility` = geodb.facility(...) of the place). Returns True when
    the window ending at this picture is busier than usual."""
    C = {**DEFAULTS, **(cfg or {})}
    if not C.get("enabled", True) or det.get("src") != SRC or typical is None or not facility:
        return False
    sites = state.setdefault("flare_watch", {})
    lat, lon, t = float(det["lat"]), float(det["lon"]), det["t"]
    site = min((s for s in sites.values() if _km(s["lat"], s["lon"], lat, lon) <= float(C["site_km"])),
               key=lambda s: _km(s["lat"], s["lon"], lat, lon), default=None)
    if site is None:
        key = f"{facility.get('lat', lat):.3f},{facility.get('lon', lon):.3f}"
        site = sites.setdefault(key, {"lat": round(float(facility.get("lat", lat)), 4),
                                      "lon": round(float(facility.get("lon", lon)), 4), "seen": {}})
    site["label"] = facility.get("label") or site.get("label") or ""
    site["typical"] = round(float(typical), 2)
    frp = det.get("frp")
    old = site["seen"].get(t)
    if frp is not None:
        site["seen"][t] = round(max(float(frp), old or 0.0), 2)
    elif t not in site["seen"]:
        site["seen"][t] = None          # the symbol was there; its strength may come later
    # this picture counts in every window that holds it (it may arrive late, or its strength may)
    at, span = parse_time(t), dt.timedelta(hours=float(C["window_h"]))
    seen = [(parse_time(x), v) for x, v in site["seen"].items()]
    busy = False
    for end in sorted({x for x, _ in seen if at <= x < at + span}):
        busy = _check(site, end, C, seen) or busy
    return busy


def _check(site, end, C, seen):
    start = end - dt.timedelta(hours=float(C["window_h"]))
    window = [(t, v) for t, v in seen if start < t <= end]
    line = strong_line(float(site["typical"]), C)
    strong = [v for _, v in window if v is not None and v >= line]
    if len(window) < int(C["min_pictures"]) or len(strong) < int(C["min_strong"]):
        return False
    ep = site.get("episode")
    if ep is None or end - parse_time(ep["last"]) > dt.timedelta(hours=float(C["new_after_h"])):
        ep = site["episode"] = {"first": iso(end), "last": iso(end), "peak": 0.0, "pictures": 0, "strong": 0}
    ep["first"], ep["last"] = min(ep["first"], iso(end)), max(ep["last"], iso(end))
    ep["peak"] = round(max(ep["peak"], max(strong)), 1)
    ep["pictures"], ep["strong"] = max(ep["pictures"], len(window)), max(ep["strong"], len(strong))
    ep["typical"], ep["window_h"] = site["typical"], float(C["window_h"])
    return True


def tidy(state, now, cfg=None):
    """Forget pictures older than the window and episodes older than what the app shows."""
    C = {**DEFAULTS, **(cfg or {})}
    sites = state.get("flare_watch", {})
    keep = now - dt.timedelta(hours=float(C["window_h"]) + 1)
    shown = now - dt.timedelta(hours=float(C["show_h"]))
    for key in list(sites):
        site = sites[key]
        site["seen"] = {t: v for t, v in site["seen"].items() if parse_time(t) >= keep}
        if site.get("episode") and parse_time(site["episode"]["last"]) < shown:
            del site["episode"]
        if not site["seen"] and not site.get("episode"):
            del sites[key]


def text(ep, label=""):
    """The app's words for one episode: what was seen, without a cause (the card shows the
    strongest reading and the usual one beside it)."""
    where = f" في {bidi_safe(label)}" if label else ""
    hours = f"{ep.get('window_h', DEFAULTS['window_h']):g}"
    return (f"اشتعلت الشعلة{where} أكثر من المعتاد: رمز حريق في {ep['pictures']} صورة خلال "
            f"{hours} ساعات، منها {ep['strong']} بقوة ضعف المعتاد أو أكثر. "
            "علامة تجريبية لا تُرسل إلى القناة؛ قد تكون حرقاً تشغيلياً للغاز أو أثر ضرر، "
            "والصور وحدها لا تفرّق بينهما.")


def items(state, now, cfg=None):
    """Episodes for the app (meta.flare_activity), newest first."""
    C = {**DEFAULTS, **(cfg or {})}
    shown = now - dt.timedelta(hours=float(C["show_h"]))
    out = []
    for site in state.get("flare_watch", {}).values():
        ep = site.get("episode")
        if not ep or parse_time(ep["last"]) < shown:
            continue
        ident = hashlib.sha1(f"{site['lat']},{site['lon']},{ep['first']}".encode()).hexdigest()[:10]
        out.append({"id": f"flare-{ident}", "lat": site["lat"], "lon": site["lon"],
                    "label": site.get("label", ""), "first": ep["first"], "last": ep["last"],
                    "peak": ep["peak"], "typical": ep["typical"], "pictures": ep["pictures"],
                    "strong": ep["strong"], "text": text(ep, site.get("label", ""))})
    out.sort(key=lambda e: e["last"], reverse=True)
    return out
