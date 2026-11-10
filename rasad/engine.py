"""One monitoring cycle: collect detections, judge them, alert, and publish the app feed."""
from __future__ import annotations

import datetime as dt
import math
import os
from contextlib import nullcontext
import sys
import time
from pathlib import Path

import yaml

from .brain import (VERDICT_RANK, Memory, add_to_incident, alert_message, finalize, find_incident,
                    nearby_confirmed, new_incident, day_requires_confirmation, association_requires_confirmation)
from .clip import NotVisible, make_clip
from .geodb import Geo
from . import seawatch
from . import backfill, geocollect, mediaqueue
from . import acquisitions, repairs
from .notify import deliver, telegram_ready, tg_send
from .sources import detect_geo_layer, fetch_frp, firms_fetch, region_tiles, union_bbox
from .updates import intensity_update_due, peak_reading
from .util import (floor_time, haversine_km, in_bbox, iso, load_json, log, make_session, parse_time,
                   save_json, utcnow)
from .wms import WMS, Availability, latest_time

STALE_HOURS = 3
RULES = 15  # bump when the importance rules change, so stored incidents are judged again
CLIP_STYLE = 18  # bump when clips change look, so the app's clips are made again
SEA_CHECK = 1    # bump when the closer look changes: clips of the sea finder's own finds are checked again
TIDY = 1         # bump to tidy the stored incidents once more (copies under one id, split recoveries)
# fire symbols whose strength (MW) the server can tell; the second European satellite's cannot
STRENGTH_SOURCES = frozenset({"mtg_frp"})

DEFAULTS = {
    "app_name": "رصد",
    "app_url": "",
    "watermark": "",
    "watermark_logo": "",
    "local_time": {"offset_hours": 3, "name": "بغداد"},
    "paths": {"state": "data/state", "site": "data/site", "geodata": "data/state/geo"},
    "regions": [],
    "alerts": {"min_verdict": "مرجّح", "at_facility_km": 2, "near_facility_km": 10, "near_facility_min_mw": 50,
               "big_fire_mw": 50, "big_fire_far_mw": 100, "busy_area_days": 2, "faint_mw": 3,
               "max_new_per_run": 6, "max_age_hours": 8,
               "intensity_ratio": 1.5, "intensity_add_mw": 1, "intensity_interval_min": 30,
               "intensity_max_age_hours": 24,
               "focus_countries": ["العراق", "السعودية", "الإمارات", "قطر", "البحرين", "الكويت", "عمان",
                                   "عُمان", "إيران", "اليمن"]},
    "memory": {"persistent_min_days": 2, "window_days": 30, "unusual_ratio": 5, "unusual_add_mw": 25,
               "unusual_facility_km": 3},
    "incidents": {"merge_km": 6, "merge_hours": 12, "keep_days": 7,
                  "max_polar_gap_hours": 3, "polar_relocation_km": 2},
    "firms": {"map_key": "", "base_url": "https://firms.modaps.eosdis.nasa.gov",
              "sources": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"],
              "bootstrap_days": 10, "max_day_range": 5, "live_hours": 36,
              "budget_s": 25, "request_timeout_s": 20, "max_workers": 2},
    "recovery": {"enabled": False, "budget_s": 15, "max_images_per_run": 2,
                 "auto_targets": True, "auto_hours": 24, "max_targets": 6,
                 "targets": []},
    "repairs": {"enabled": False, "cases": []},
    "telegram": {"bot_token": "", "chat_id": "", "api_base": "https://api.telegram.org"},
    "eumetview": {
        "wms_url": "https://view.eumetsat.int/geoserver/wms",
        "tile_deg": 8,
        "tile_px": 1000,
        "lookback_slots": 2,
        "settle_minutes": 25,
        # after an outage at the source the pictures come out late: slots of the last hours that
        # were never read are read then (a little per run)
        "catchup_hours": 2, "catchup_budget_s": 75,
        "scan_budget_s": 65, "scan_max_images": 20, "scan_workers": 4, "scan_timeout_s": 20,
        "strength_budget_s": 20,
        "fire_layers": [
            {"id": "mtg_frp", "name": "mtg_fd:frp", "family": "mtg", "cadence_min": 10,
             "clock_layer": "mtg_fd:ir105_hrfi", "label_ar": "القمر الأوروبي Meteosat-12"},
            {"id": "iodc_fire", "name": "msg_iodc:fire", "family": "msg_iodc", "cadence_min": 15,
             "clock_layer": "msg_iodc:ir039", "label_ar": "القمر الأوروبي فوق المحيط الهندي"},
        ],
        "clip_layer": {"name": "msg_iodc:ir039", "label": "Meteosat IODC IR3.9", "style": "",
                       "cadence_min": 15, "hot_is_bright": "auto", "hot_is_bright_fallback": False},
        "land_layer": "osmgray:land_polygons",
        "land_style": "",
    },
    "geodata": {"facility_max_km": 30, "place_max_km": 80, "coast_km": 1.5, "query_tile_deg": 8,
                "rebuild_days": 7,
                "overpass_urls": ["https://overpass-api.de/api/interpreter",
                                  "https://overpass.kumi.systems/api/interpreter"],
                "nominatim_url": "https://nominatim.openstreetmap.org/reverse"},
    "clip": {"enabled": True, "frames": 8, "frames_after": 2, "half_size_km": 20, "px_km": 0.25,
             "smooth_km": 0.6, "out_px": 1080, "fps": 2, "hold_last": 4, "format": "mp4", "per_run": 1,
             "layout": "two", "context_layer": "mtg_fd:rgb_geocolour", "out_fps": 24,
             "reveal_s": 1.6, "hud_title": "ALFAQAAR FIRMS",
             # the faint slanted logo pattern over the pictures (as in the first intelligence clips)
             "logo_tile_width": 150, "logo_tile_opacity": 0.05, "logo_tile_gap": [130, 120],
             "logo_tile_angle": 22, "logo_tile_speed": 2.0,
             # the circle moves only onto a small new hot speck this close to the reported position
             "snap_km": 6, "snap_km_day": 4, "speck_max_km2": 40, "static_min": 25,
             # a speck longer than this is a streak (a cloud band's edge), not a fire; at sea at night
             # the speck must also be this much hotter (red, 0-255) than the clear sea around it
             "speck_max_extent_km": 8, "speck_above_sea": 8,
             # New measurements may arrive before their pictures: retry refreshed clips later.
             "refresh_retry_min": 10},
    # Own image finders: nighttime alerts and experimental daytime candidates for review.
    "seawatch": {
        "enabled": True, "alerts": True, "look_back": True, "look_back_km_per_px": 0.5,
        "layer": "mtg_fd:rgb_firetemperature", "check_layer": "mtg_fd:ir105_hrfi",
        "label": "Meteosat-12 Fire Temperature",
        "cadence_min": 10, "delay_min": 15, "slots_per_run": 6, "km_per_px": 0.5,
        # slots missed in the last hours (the source late or down) are checked later, newest first,
        # within this many seconds per run
        "catchup_hours": 3, "budget_s": 240,
        "ref_minutes": [30], "min_rise": 6.0, "min_z": 5.0, "max_area_km2": 60, "max_was_hot": 4.0,
        # low cloud or fog drifting off is not a fire: the speck must be hotter than the clear sea
        # around it, and its warming no longer than a few km (a cloud edge runs on for tens of km)
        "min_above_sea": 3.0, "max_warm_extent_km": 8.0,
        "min_coast_km": 3.0, "platform_km": 3.0, "known_heat_km": 10.0, "max_z_ir105": 6.0,
        "sparkle_min_days": 3,
        "sun_max_elev": -6.0,
        # These thresholds are image display levels, never Kelvin or fire power in MW.
        # Day candidates need independent official/NASA evidence before any Telegram alert.
        "daytime": {
            "enabled": True, "ref_minutes": [30, 60],
            "min_sun_elev": 6.0, "min_view_elev": 10.0, "min_glint_angle": 25.0,
            "min_rise": 12.0, "min_z": 8.0, "min_above_sea": 12.0, "max_was_hot": 8.0,
            "max_area_km2": 40.0, "max_warm_extent_km": 8.0,
            "max_blue_contrast": 12.0, "max_blue_rise": 10.0,
            "max_ir_change_z": 3.0, "max_ir_cold_excess": 20.0,
            "max_candidates": 6, "burst_max": 3,
        },
        # areas switched on only after a replay of a real night showed no false fires there
        # (1 Nov 2026: Hormuz/Gulf of Oman and the southern Gulf clean, the fire caught at 00:50 and
        # 01:10; the northern Gulf oil fields sparkle all night, so they stay off)
        "boxes": [
            {"id": "hormuz_oman", "bbox": [55.0, 22.4, 60.2, 27.2]},
            {"id": "gulf_south", "bbox": [50.5, 23.8, 55.2, 27.8]},
            {"id": "gulf_north", "bbox": [47.8, 27.8, 51.5, 30.5], "enabled": False},
            {"id": "arabian_sea", "bbox": [52.0, 16.4, 60.2, 22.5], "enabled": False},
            {"id": "red_sea_north", "bbox": [32.2, 26.5, 36.0, 30.0], "enabled": False},
            {"id": "red_sea_middle", "bbox": [34.0, 20.0, 40.5, 27.5], "enabled": False},
            {"id": "red_sea_south", "bbox": [38.0, 12.0, 44.5, 20.5], "enabled": False},
        ],
    },
}


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path, state_dir=None, site_dir=None):
    p = Path(path)
    if not p.exists():
        sys.exit(f"Config file not found: {p}")
    with open(p, encoding="utf-8") as f:
        cfg = deep_merge(DEFAULTS, yaml.safe_load(f) or {})
    base = p.resolve().parent

    def where(x):
        x = Path(x)
        return x if x.is_absolute() else base / x

    cfg["_state_dir"] = Path(state_dir) if state_dir else where(cfg["paths"]["state"])
    cfg["_site_dir"] = Path(site_dir) if site_dir else where(cfg["paths"]["site"])
    cfg["_geodata_dir"] = (cfg["_state_dir"] / "geo") if state_dir else where(cfg["paths"]["geodata"])
    # secrets may come from the environment (GitHub Actions secrets)
    env = os.environ
    if env.get("FIRMS_MAP_KEY"):
        cfg["firms"]["map_key"] = env["FIRMS_MAP_KEY"]
    if env.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = env["TELEGRAM_BOT_TOKEN"]
    if env.get("TELEGRAM_CHAT_ID"):
        cfg["telegram"]["chat_id"] = env["TELEGRAM_CHAT_ID"]
    if env.get("RASAD_APP_URL"):
        cfg["app_url"] = env["RASAD_APP_URL"]
    cfg["firms"]["map_key"] = str(cfg["firms"].get("map_key") or "").strip()
    cfg["_background_media"] = env.get("RASAD_BACKGROUND_MEDIA") == "1"
    regions = []
    for r in cfg.get("regions") or []:
        if r.get("enabled", True):
            regions.append({"id": str(r["id"]), "name": str(r.get("name", r["id"])),
                            "bbox": [float(v) for v in r["bbox"]]})
    if not regions:
        sys.exit("No regions enabled in the config.")
    cfg["regions"] = regions
    return cfg


class State:
    """Everything the engine remembers between runs (plain JSON files)."""

    def __init__(self, cfg):
        self.dir = Path(cfg["_state_dir"])
        self.sent = False  # something went out to the channel in this check
        self.state = load_json(self.dir / "state.json", {})
        self.memory = load_json(self.dir / "memory.json", {})
        self.incidents = load_json(self.dir / "incidents.json", [])

    def save(self):
        save_json(self.dir / "state.json", self.state, compact=True)
        save_json(self.dir / "memory.json", self.memory, compact=True)
        save_json(self.dir / "incidents.json", self.incidents, compact=True)
        if self.sent:  # tells the watch loop to store the memory on GitHub straight away
            (self.dir.parent / ".state_urgent").write_text("1")


def _merge_records(keep, other):
    """Fold the evidence of another record of the same fire into the one that is kept."""
    keep["first"], keep["last"] = min(keep["first"], other["first"]), max(keep["last"], other["last"])
    for key in ("srcs", "families"):
        for value in other.get(key) or []:
            if value not in keep.setdefault(key, []):
                keep[key].append(value)
    keep["geo_slots"] = sorted(set(keep.get("geo_slots") or []) | set(other.get("geo_slots") or []))[-60:]
    if keep.get("own_night_slots") or other.get("own_night_slots"):
        keep["own_night_slots"] = sorted(set(keep.get("own_night_slots") or [])
                                         | set(other.get("own_night_slots") or []))[-60:]
    for key, limit in (("observations", 120), ("polar", 20)):
        rows = keep.setdefault(key, [])
        for row in other.get(key) or []:
            if row not in rows:
                rows.append(row)
        keep[key] = sorted(rows, key=lambda r: r["t"])[-limit:]
    series = keep.setdefault("frp_series", [])
    for row in other.get("frp_series") or []:
        if row not in series:
            series.append(row)
    keep["frp_series"] = sorted(series)[-40:]
    powers = [v for v in (keep.get("max_frp"), other.get("max_frp")) if v is not None]
    keep["max_frp"] = max(powers) if powers else None
    if other.get("best") == "polar" and keep.get("best") != "polar":
        keep["lat"], keep["lon"], keep["best"] = other["lat"], other["lon"], "polar"
    if keep.get("kind") != "unusual" and other.get("kind") == "unusual":
        keep["kind"] = "unusual"
    for key in ("known_days", "typical_frp", "area_days"):
        if keep.get(key) is None and other.get(key) is not None:
            keep[key] = other[key]
    keep["n"] = len(keep["observations"]) or max(keep.get("n") or 1, other.get("n") or 1)


def tidy_incidents(S, cfg, now):
    """Repair the memory left by an earlier version: one fire stored many times under the same
    id (a symbol waiting for its strength was judged again every check), and a recovered past
    event split into one record per picture. Runs on every check for the first kind (cheap),
    once for the second."""
    alerted = ("alerted_at", "alerted_verdict", "tg_msg", "alerted_max_frp", "alert_updated_at")
    groups = {}
    for inc in S.incidents:
        groups.setdefault(inc["id"], []).append(inc)
    copies = sum(len(g) - 1 for g in groups.values())
    if copies:
        kept = []
        for inc_id, group in groups.items():
            # keep the record that went out to the channel, if one did (its post is answered later)
            keep = next((i for i in group if i.get("alerted_at")), group[0])
            for other in group:
                if other is not keep:
                    _merge_records(keep, other)
            kept.append(keep)
        order = {inc_id: k for k, inc_id in enumerate(groups)}
        S.incidents = sorted(kept, key=lambda i: order[i["id"]])
        log.warning("memory tidied: %d extra copies of %d incidents merged",
                    copies, sum(1 for g in groups.values() if len(g) > 1))
    if S.state.get("tidy") == TIDY:
        return
    for inc in S.incidents:   # counts inflated by the same symbol judged again every check
        rows = inc.get("observations") or []
        if rows and len(rows) < 120 and (inc.get("n") or 0) > len(rows):
            inc["n"] = len(rows)
    merge_km, merge_h = float(cfg["incidents"]["merge_km"]), float(cfg["incidents"]["merge_hours"])
    window = dt.timedelta(hours=merge_h)
    split = sorted((i for i in S.incidents if i.get("recovered_historical") and not i.get("alerted_at")),
                   key=lambda i: i["first"])
    gone = set()
    for k, keep in enumerate(split):
        if keep["id"] in gone:
            continue
        for other in split[k + 1:]:
            if other["id"] in gone or other.get("kind") != keep.get("kind"):
                continue
            if parse_time(other["first"]) - parse_time(keep["last"]) > window:
                continue
            if haversine_km(keep["lat"], keep["lon"], other["lat"], other["lon"]) <= merge_km:
                _merge_records(keep, other)
                gone.add(other["id"])
    if gone:
        S.incidents = [i for i in S.incidents if i["id"] not in gone]
        for inc in split:
            if inc["id"] not in gone:
                finalize(inc, cfg)
        log.warning("memory tidied: %d recovered pieces joined to their event", len(gone))
    S.state["tidy"] = TIDY


def bootstrap(cfg, session, S, box):
    """Learn the usual heat (gas flares, plants) from NASA data of the previous days.
    Yesterday and today are left out on purpose: they are judged as live data, so a fire
    from last night is reported instead of being learned as 'usual'.
    NASA accepts at most 5 days per request, so the period is read in chunks."""
    days = int(cfg["firms"].get("bootstrap_days", 10))
    chunk = int(cfg["firms"].get("max_day_range", 5))
    mem = Memory(S.memory)
    today = utcnow().date()
    first, last = today - dt.timedelta(days=days), today - dt.timedelta(days=2)
    lo, hi = first.isoformat(), last.isoformat()
    total, span, cur = 0, set(), first
    while cur <= last:
        n = min(chunk, (last - cur).days + 1)
        for d in firms_fetch(session, cfg, box, n, date=cur.isoformat()):
            day = d["t"][:10]
            if lo <= day <= hi:
                mem.record(d["lat"], d["lon"], day, d.get("frp"))
                span.add(day)
                total += 1
        cur += dt.timedelta(days=n)
    log.info("learned usual heat from %d NASA detections on %d days (%s .. %s)", total, len(span),
             min(span, default="-"), max(span, default="-"))
    return total


def smallest_region(cfg, lat, lon):
    inside = [r for r in cfg["regions"] if in_bbox(r["bbox"], lat, lon)]
    if not inside:
        return None, []
    inside.sort(key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]))
    return inside[0], [r["id"] for r in inside]


def enrich(inc, cfg, geo):
    """Where is it, what is near it, which region: only redone when the position moves."""
    pos = [round(inc["lat"], 3), round(inc["lon"], 3), geo.version]
    if inc.get("_pos") != pos:  # redo when the fire moved or the facility map grew
        geo.naming_deferred = False
        inc["place"] = geo.place_kind(inc["lat"], inc["lon"])
        inc["facility"] = geo.facility(inc["lat"], inc["lon"])
        inc["where"], inc["country"] = geo.describe(inc["lat"], inc["lon"], inc["place"])
        if not getattr(geo, "naming_deferred", False):
            inc["_pos"] = pos
    region, ids = smallest_region(cfg, inc["lat"], inc["lon"])  # cheap: follows renamed regions
    inc["regions"] = ids
    inc["region_name"] = region["name"] if region else None
    return finalize(inc, cfg)


def clip_layers(cfg):
    """Pictures to try for a clip, best first. Meteosat-12's fire picture (every 10 minutes, finer
    pixels) shows small fires that the older IODC picture smears out (the Hormuz ship fire of
    1 Nov 2026 shows only on Meteosat-12)."""
    E, W = cfg["eumetview"], cfg["seawatch"]
    # Use only the displayed red channel: the other channels include solar reflections.
    # This WMS composite has no validated temperature calibration; do not infer power from it.
    mtg = dict(E["clip_layer"], name=W["layer"], cadence_min=W["cadence_min"], label=W["label"],
               hot_is_bright=True, channel=0)
    mtg.pop("bt", None)
    return [mtg, E["clip_layer"]]


def clip_for(cfg, wms, S, inc, focus_time=None):
    import hashlib
    import json
    from .updates import peak_reading

    peak = peak_reading(inc)
    focus = parse_time(focus_time) if focus_time is not None else \
        (parse_time(peak[0]) if peak else None)
    measurements = set()
    rows = [r[:2] for r in inc.get("frp_series") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    rows += [[p.get("t"), p.get("frp")] for p in inc.get("polar") or [] if isinstance(p, dict)]
    for when, value in rows:
        try:
            if value is not None and not isinstance(value, bool):
                mw = float(value)
                if math.isfinite(mw) and mw >= 0:
                    measurements.add((iso(parse_time(when)), mw))
        except (TypeError, ValueError, OverflowError):
            continue
    err = "no pictures"
    for L in clip_layers(cfg):
        cad = int(L["cadence_min"])
        pbox = [inc["lon"] - 0.2, inc["lat"] - 0.2, inc["lon"] + 0.2, inc["lat"] + 0.2]
        latest = latest_time(wms, S.state, L["name"], cad, pbox)
        if latest is None or utcnow() - latest > dt.timedelta(hours=STALE_HOURS):
            err = f"{L['name']}: no recent pictures"
            continue
        if focus is not None and latest < floor_time(focus, cad):
            err = f"{L['name']}: pictures of the new measurement are not ready"
            continue
        C = cfg["clip"]
        after = int(C.get("frames_after", 2))
        start = floor_time(parse_time(inc["first"]), cad)
        t_end = start + dt.timedelta(minutes=cad * after)
        # Include the strongest recorded measurement even when it arrives hours after the
        # start. The frame cap below keeps a long-running incident's update near its peak.
        if focus is not None:
            t_end = max(t_end, floor_time(focus, cad) + dt.timedelta(minutes=cad))
        t_end = min(latest, t_end)
        n_before = max(2, int(C.get("frames", 8)) - after - 1)
        frames = min(16, n_before + 1 + max(0, int((t_end - start).total_seconds() // (cad * 60))))
        fac = inc.get("facility")
        info = {"where": inc.get("where"), "region": inc.get("region_name"), "verdict": inc.get("verdict"),
                "country": inc.get("country"), "place": inc.get("place"),
                "size": inc.get("size"), "first": inc.get("first"),
                "frp": [list(r) for r in sorted(measurements)],
                "facility": fac["text"] if fac and fac.get("km", 99) <= 15 else None}
        # The cloud publisher uploads new names only. A changed clip must have a new
        # content revision so an existing remote video and browser cache cannot hide it.
        revision = {"style": CLIP_STYLE, "layer": L["name"], "end": iso(t_end),
                    "info": info,
                    "where": inc.get("where"), "verdict": inc.get("verdict"),
                    "first": inc.get("first"), "last": inc.get("last"),
                    "frp": info["frp"], "polar": inc.get("polar") or []}
        digest = hashlib.sha256(json.dumps(revision, sort_keys=True, default=str).encode()).hexdigest()[:12]
        base = Path(cfg["_site_dir"]) / "clips" / f"{inc['id']}-{digest}"
        try:
            return make_clip(dict(cfg, eumetview=dict(cfg["eumetview"], clip_layer=L)), wms,
                             inc["lat"], inc["lon"], t_end, base, frames=frames, info=info)
        except NotVisible as e:
            err = e  # try the other satellite; if neither shows it, say so instead of a misleading clip
        except Exception as e:  # noqa: BLE001
            err = RuntimeError(f"{L['name']}: {e}")
    raise err if isinstance(err, Exception) else RuntimeError(err)


def _clip_measurement_changed(inc):
    """Whether a stored clip is behind a real measured peak, independent of alerts."""
    peak = peak_reading(inc)
    if not inc.get("clip") or peak is None:
        return False
    if inc.get("clip_peak_time") != peak[0]:
        return True
    try:
        covered = float(inc.get("clip_max_frp"))
    except (TypeError, ValueError, OverflowError):
        return True
    # The public series rounds to one decimal; avoid refreshing 13.39 MW as 13.4 MW.
    return not math.isfinite(covered) or covered < 0 or peak[1] > covered + 0.05


def add_clip(cfg, wms, S, inc, now, refresh=False):
    """Make or refresh a clip; preserve the previous usable media if a refresh fails."""
    from .updates import peak_reading

    if not cfg["clip"].get("enabled", True):
        return False
    refresh = refresh or bool(inc.get("clip_needs_refresh"))
    peak = peak_reading(inc)
    if inc.get("clip"):
        if not refresh:
            return True
        try:
            up_to_date = ((Path(cfg["_site_dir"]) / inc["clip"]).is_file()
                          and inc.get("clip_style") == CLIP_STYLE
                          and inc.get("clip_peak_time") == (peak[0] if peak else None)
                          and float(inc.get("clip_max_frp")) >= float(inc.get("max_frp") or 0))
        except (TypeError, ValueError):
            up_to_date = False
        if up_to_date:
            inc.pop("clip_needs_refresh", None)
            return True
    tried_key = "clip_refresh_tried" if refresh else "clip_tried"
    tried = inc.get(tried_key)
    retry_min = float(cfg["clip"].get("refresh_retry_min", 10)) if refresh else 60
    if tried and now - parse_time(tried) < dt.timedelta(minutes=retry_min):
        return False
    inc[tried_key] = iso(now)
    try:
        if refresh:
            video, snap = clip_for(cfg, wms, S, inc, focus_time=peak[0] if peak else inc["last"])
        else:
            video, snap = clip_for(cfg, wms, S, inc)
        site = Path(cfg["_site_dir"])
        # Replace both references only once rendering has succeeded.
        video_name = Path(video).relative_to(site).as_posix()
        snap_name = Path(snap).relative_to(site).as_posix()
        inc["clip"], inc["snap"] = video_name, snap_name
        try:
            covered = float(inc.get("max_frp"))
            if not math.isfinite(covered) or covered < 0:
                raise ValueError("invalid peak")
        except (TypeError, ValueError, OverflowError):
            covered = peak[1] if peak else None
        inc["clip_max_frp"] = covered
        inc["clip_peak_time"] = peak[0] if peak else None
        inc["clip_updated_at"], inc["clip_style"] = iso(now), CLIP_STYLE
        inc.pop("clip_needs_refresh", None)
        inc.pop("clip_note", None)
        if inc.pop("unseen_close", None):
            finalize(inc, cfg)
        return True
    except NotVisible:
        if refresh:
            log.info("updated clip for %s: the new heat is not visible yet; keeping previous media", inc["id"])
            return False
        if inc.get("srcs") and set(inc["srcs"]) <= seawatch.FINDER_SOURCES:
            # found by Rasad's own sea finder only, and the closer look does not show it either
            inc["clip_note"] = ("وجده الباحث الحراري في صور القمر الأوروبي، لكنه لا يظهر بوضوح عند التدقيق في "
                                "الصور القريبة، وقد يكون تغيّراً في الغيوم أو الضباب لا حريقاً. ننتظر مرور قمر أمريكي")
            inc["unseen_close"] = True
            finalize(inc, cfg)
        elif inc.get("polar"):
            inc["clip_note"] = "لا يظهر هذا الحريق في صور القمر الأوروبي (صغير أو قصير المدة)، رصدته ناسا وحدها"
        else:
            inc["clip_note"] = "لا يظهر هذا الحريق بوضوح في الصور القريبة للقمر الأوروبي، وننتظر مرور قمر أمريكي"
        log.info("clip for %s: the fire does not show in the European pictures", inc["id"])
    except Exception as e:  # noqa: BLE001
        if not refresh:
            inc["clip_note"] = "تعذر تجهيز الفيديو حالياً؛ ستُعاد المحاولة تلقائياً عند توفر صور صالحة"
        log.warning("clip for %s failed: %s", inc["id"], e)
    return False


def _sent_peak(inc):
    """Snapshot the peak actually included in a successful post, not its latest sample."""
    peak = peak_reading(inc)
    values = [peak[1]] if peak is not None else []
    try:
        value = float(inc.get("max_frp"))
        if math.isfinite(value) and value >= 0:
            values.append(value)
    except (TypeError, ValueError, OverflowError):
        pass
    return max(values) if values else None


def _remember_post(inc, now):
    inc["alerted_max_frp"] = _sent_peak(inc)
    inc["alert_updated_at"] = iso(now)


def _delayed_update(inc, cfg, now):
    peak = peak_reading(inc)
    return peak is not None and now - parse_time(peak[0]) > dt.timedelta(
        hours=float(cfg["alerts"].get("max_age_hours", 8)))


def run_cycle(cfg, send=True, alert_now=False):
    now = utcnow()
    started_clock = time.monotonic()
    today = now.date().isoformat()
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    S = State(cfg)
    tidy_incidents(S, cfg, now)
    repairs.apply_reviewed(cfg, S, now)
    if cfg.get("_background_media"):
        mediaqueue.consume_results(cfg, S, now)
    geo = Geo(cfg, session, wms, S.state)
    if cfg.get("_background_media"):
        method = getattr(type(wms), "budget", None)
        with wms.budget(8, timeout_s=4) if callable(method) else nullcontext():
            geo.self_test()
    else:
        geo.self_test()
    E, M, I = cfg["eumetview"], cfg["memory"], cfg["incidents"]
    regions = cfg["regions"]
    box = union_bbox([r["bbox"] for r in regions])
    tiles = region_tiles(regions, float(E["tile_deg"]))
    status = {}

    if S.state.get("schema", 1) < 3:
        # the first learning step had failed and the sea/land test was wrong:
        # the incidents made with them were false alarms, so start them again
        S.incidents = []
        for k in ("bootstrapped", "landsea", "seen"):
            S.state.pop(k, None)
        S.state["schema"] = 3
    if cfg["firms"]["map_key"] and not S.state.get("bootstrapped"):
        if bootstrap(cfg, session, S, box) > 0:
            S.state["bootstrapped"] = iso(now)

    if telegram_ready(cfg) and not S.state.get("hello_sent"):
        active = Memory(S.memory).days_known(today, int(M["window_days"])) > 0 or alert_now
        hello = ("✅ <b>رصد يعمل الآن</b>\n"
                 f"يراقب: {'، '.join(r['name'] for r in regions)}\n"
                 "ستصلك هنا تنبيهات الحرائق الجديدة في البحر وقرب منشآت الطاقة، مع مقطع القمر الصناعي.\n"
                 + ("التنبيهات تعمل من الآن." if active else
                    "اليوم يتعلّم الحرارة المعتادة، وتبدأ التنبيهات غداً. أضف مفتاح ناسا لتبدأ فوراً."))
        if cfg.get("app_url"):
            hello += f'\n<a href="{cfg["app_url"]}">افتح التطبيق</a>'
        if tg_send(cfg, session, hello):
            S.state["hello_sent"] = iso(now)

    # Live tiles and outage catch-up share a bounded, resumable collector.
    slots = S.state.setdefault("slots", {})
    avail = Availability(wms, S.state, now)
    dets, status, n_images = geocollect.collect(
        cfg, wms, S.state, now, tiles, avail, detect=detect_geo_layer, latest=latest_time)

    # 1b) Own sea finders: nighttime checks and separately guarded daytime candidates.
    SW = cfg["seawatch"]
    if SW.get("enabled"):
        t1 = time.time()
        try:
            watch_cfg = cfg
            if cfg.get("_background_media"):
                watch_cfg = dict(cfg, seawatch=dict(SW, budget_s=min(float(SW.get("budget_s", 240)), 35),
                                                  slots_per_run=min(int(SW["slots_per_run"]), 2)))
            seconds = float(watch_cfg["seawatch"].get("budget_s", 240))
            budget = getattr(type(wms), "budget", None)
            with wms.budget(seconds, timeout_s=20) if callable(budget) else nullcontext():
                spots = seawatch.watch(watch_cfg, wms, S.state, now, Memory(S.memory), geo, avail)
            ok = True
        except Exception as e:  # noqa: BLE001
            log.warning("sea watch failed: %s", e)
            spots, ok = [], False
        dets += spots
        status["seawatch"] = {"name": "كاشف رصد للبحر ليلاً (Meteosat-12)", "ok": ok,
                              "latest": max((d["t"] for d in spots if d["src"] == seawatch.SRC), default=None)}
        if SW.get("daytime", {}).get("enabled"):
            day_status = S.state.get("sea_day_status", {})
            checked_at = day_status.get("last_checked")
            fresh_day = (checked_at and now - parse_time(checked_at) <=
                         dt.timedelta(minutes=float(SW["delay_min"]) + 3 * float(SW["cadence_min"])))
            status["seawatch_day"] = {
                **day_status, "name": "كاشف رصد النهاري التجريبي (Meteosat-12)",
                "ok": bool(ok and fresh_day and not day_status.get("missing")),
                "latest": checked_at, "experimental": True,
            }
        log.info("sea watch: %d spot(s), %.0f s", len(spots), time.time() - t1)

    # 2) NASA (VIIRS/MODIS), precise but a few hours late.
    #    Ask for today AND yesterday (UTC): the night passes over the Gulf (about 21:30-23:30 UTC)
    #    are published after midnight UTC, so "today only" would never see them.
    if cfg["firms"]["map_key"]:
        seen = S.state.setdefault("seen", {})
        nasa_status = {}
        nasa_cfg = dict(cfg, firms=dict(cfg["firms"], source_offset=int(S.state.get("firms_source_offset", 0))))
        polar = firms_fetch(session, nasa_cfg, box, 2, status=nasa_status)
        S.state["firms_source_offset"] = (int(S.state.get("firms_source_offset", 0)) + 2) % max(1, len(cfg["firms"]["sources"]))
        live_from = now - dt.timedelta(hours=float(cfg["firms"].get("live_hours", 36)))
        n_new = n_old = 0
        for d in polar:
            k = f"{d['src']}|{d['t']}|{d['lat']}|{d['lon']}"
            if k in seen:
                continue
            seen[k] = d["t"]
            if parse_time(d["t"]) < live_from:
                d["_history"] = True  # too old to report: only remembered as heat history
                n_old += 1
            else:
                n_new += 1
            dets.append(d)
        log.info("NASA: %d detections in the last 2 days, %d new to judge, %d kept as history",
                 len(polar), n_new, n_old)
        status["firms"] = {**nasa_status, "name": "الأقمار الأمريكية (ناسا)",
                           "ok": bool(nasa_status.get("ok")),
                           "latest": max((d["t"] for d in polar), default=None)}

    # Historical recovery asks the official product again; daily memory is only a search hint.
    recovered, recovery_status = backfill.collect(cfg, wms, S.state, Memory(S.memory), geo, now,
                                                 detect=detect_geo_layer, fetch=fetch_frp)
    dets += recovered
    if recovery_status["enabled"]:
        status["recovery"] = dict(recovery_status, name="استرجاع رصد سابق قرب المنشآت")

    # European symbols whose strength could not be read yet are asked again here, a few per
    # check. Only a symbol whose strength has now arrived goes back to the judgement (that is
    # new information: a usual flare may turn out to burn far hotter). One still without
    # strength is not judged again: it is already part of its incident.
    strength_pending = S.state.setdefault("strength_pending", {})
    for key, det in list(strength_pending.items()):
        if now - parse_time(det["t"]) > dt.timedelta(hours=24) or det.get("src") not in STRENGTH_SOURCES:
            del strength_pending[key]
    retry_deadline = time.monotonic() + float(E.get("strength_retry_budget_s", 10))
    retry_left = int(E.get("strength_retry_max", 15))
    for key, det in sorted(strength_pending.items(), key=lambda kv: kv[1]["t"], reverse=True):
        remaining = retry_deadline - time.monotonic()
        if remaining <= 0 or retry_left <= 0:
            break
        retry_left -= 1
        again = dict(det)
        method = getattr(type(wms), "budget", None)
        with wms.budget(remaining, timeout_s=8) if callable(method) else nullcontext():
            fetch_frp(wms, again)
        if again.get("frp") is not None:
            del strength_pending[key]
            dets.append(again)

    # drop duplicates of the same symbol found in two neighbouring tiles
    uniq = {}
    for d in dets:
        # VIIRS pixels can share a 0.01-degree cell. Preserve their distinct
        # measured powers and positions instead of retaining an arbitrary first pixel.
        uniq.setdefault((d["src"], d["t"], round(d["lat"], 4), round(d["lon"], 4)), d)
    dets = sorted(uniq.values(), key=lambda d: d["t"])

    # 3) judgement
    mem = Memory(S.memory)
    avail = mem.days_known(today, int(M["window_days"]))  # days of real history, not days the bot ran
    learning = avail == 0 and not alert_now
    merge_km, merge_h = float(I["merge_km"]), float(I["merge_hours"])
    # Decide the association once for all connected pixels of one pass. A CSV's
    # row order must not split a pass or let its last pixel join another incident.
    grouped = acquisitions.groups(
        [d for d in dets if any(in_bbox(r["bbox"], d["lat"], d["lon"]) for r in regions)], merge_km)
    membership = {id(det): group for group in grouped for det in group}
    dets = [det for group in grouped for det in group]
    group_matches = {}
    # A detection is matched against incidents near its own time. Most are fresh (the last
    # hours), but a late picture, a recovered past slot or a strength read later can be up to
    # days old: those look at the whole memory, so they join their incident instead of
    # starting a copy of it.
    fresh_from = now - dt.timedelta(hours=merge_h)
    open_incs = [i for i in S.incidents if parse_time(i["last"]) >= fresh_from - dt.timedelta(hours=merge_h)]
    by_id = {i["id"]: i for i in S.incidents}

    def candidates(det):
        return open_incs if parse_time(det["t"]) >= fresh_from else S.incidents

    touched, frp_budget = set(), 40
    strength_deadline = time.monotonic() + float(E.get("strength_budget_s", 20))

    def strength_key(det):
        return f"{det['src']}|{det['t']}|{det['lat']}|{det['lon']}"

    def read_strength(det):
        nonlocal frp_budget
        fi = det.get("_fi")
        if not fi or det.get("frp") is not None or det.get("src") not in STRENGTH_SOURCES:
            return  # the second European satellite's fire symbols carry no strength at all
        remaining = strength_deadline - time.monotonic()
        if remaining > 0 and frp_budget > 0:
            frp_budget -= 1
            method = getattr(type(wms), "budget", None)
            with wms.budget(remaining, timeout_s=8) if callable(method) else nullcontext():
                fetch_frp(wms, det)
        if det.get("frp") is None:
            det["_fi"] = fi
            strength_pending[strength_key(det)] = dict(det)
        else:
            strength_pending.pop(strength_key(det), None)

    for d in dets:
        if not any(in_bbox(r["bbox"], d["lat"], d["lon"]) for r in regions):
            continue
        day = d["t"][:10]
        experimental_day = d["src"] == seawatch.DAY_SRC
        if d.get("_history"):
            if not experimental_day:
                mem.record(d["lat"], d["lon"], day, d.get("frp"))
            continue
        # European pixels are coarse (Meteosat-12 sees the Gulf at a steep angle): look wider around
        reach = {"mtg": 3, "msg_iodc": 2}.get(d["family"], 2) if d["kind"] == "geo" else 1
        n_days, typical = mem.profile(d["lat"], d["lon"], day, int(M["window_days"]), reach)
        known = avail > 0 and n_days >= min(int(M["persistent_min_days"]), avail)
        # 'unusual' is for steady heat (a flare seen most days) at a real facility that suddenly
        # burns far hotter, not for farm areas that burn now and then
        steady = known and n_days >= max(3, math.ceil(0.5 * min(avail, int(M["window_days"]))))
        frp = d.get("frp")  # NASA gives the strength directly; European symbols are asked later
        steady_facility = None
        # A known European symbol needs its strength before it can be dismissed as routine.
        # Limit these extra queries to steady heat at facilities, where a surge matters.
        if steady and typical is not None and frp is None and d["kind"] == "geo" \
                and d.get("_fi"):
            steady_facility = geo.facility_within(d["lat"], d["lon"], float(M["unusual_facility_km"]))
            if steady_facility:
                read_strength(d)
                frp = d.get("frp")
        unusual = steady and frp is not None and typical is not None and \
            frp >= max(float(M["unusual_ratio"]) * typical, typical + float(M["unusual_add_mw"])) and \
            (steady_facility if steady_facility is not None else
             geo.facility_within(d["lat"], d["lon"], float(M["unusual_facility_km"])))
        routine = known and not unusual
        if not experimental_day:
            mem.record(d["lat"], d["lon"], day, frp)
        group = membership[id(d)]
        group_id = id(group)
        if d["kind"] == "polar":
            if group_id not in group_matches:
                group_matches[group_id] = acquisitions.find_group(
                    candidates(d), group, 2.0 if routine else merge_km, merge_h, rules=I)
            inc = group_matches[group_id]
        else:
            inc = find_incident(candidates(d), d, 2.0 if routine else merge_km, merge_h, rules=I)
        if d.get("_no_alert") and inc is not None and inc.get("alerted_verdict") is not None:
            continue  # recovery fills missing incidents; it cannot rewrite an already-sent event
        if inc is None and (routine or learning):
            continue
        if inc is not None and routine and inc["kind"] == "unusual":
            continue  # the usual flare is not evidence of the unusual heat
        if d["kind"] == "geo" and d.get("_fi"):
            read_strength(d)
        d.pop("_fi", None)
        if inc is None:
            fresh = new_incident(d, "unusual" if unusual else "new")
            inc = by_id.get(fresh["id"])   # never a second record under the same id
            if inc is None:
                inc = fresh
                inc["known_days"], inc["typical_frp"] = n_days, typical
                # days with heat within ~10 km: farmland burnt every season and flare fields are busy
                inc["area_days"] = mem.profile(d["lat"], d["lon"], day, int(M["window_days"]), 5)[0]
                S.incidents.append(inc)
                open_incs.append(inc)
                by_id[inc["id"]] = inc
            if d["kind"] == "polar":
                group_matches[group_id] = inc
        if d.get("_no_alert"):
            inc["recovered_historical"] = True
        elif not d.get("_recovered") and now - parse_time(d["t"]) <= dt.timedelta(hours=8):
            inc.pop("recovered_historical", None)
        add_to_incident(inc, d)
        touched.add(inc["id"])
    log.info("judged %d detections: %d incidents touched, %d heat cells remembered",
             len(dets), len(touched), len(mem.cells))
    if learning:
        log.info("learning period: recording the usual heat only (no alerts until tomorrow)")

    # 4) describe, decide, alert
    min_rank = VERDICT_RANK.get(cfg["alerts"]["min_verdict"], 1)
    max_age = dt.timedelta(hours=float(cfg["alerts"].get("max_age_hours", 8)))
    update_max_age = dt.timedelta(hours=float(cfg["alerts"].get("intensity_max_age_hours", 24)))
    first_alerts, updates = [], []
    look_budget = 0 if cfg.get("_background_media") else 2
    post_deadline = time.monotonic() + 20

    def post(message, media=None, reply_to=None):
        post_cfg = cfg
        if cfg.get("_background_media"):
            remaining = post_deadline - time.monotonic()
            if remaining <= 0:
                return None  # notification state is unchanged; retry next completed scan
            post_cfg = dict(cfg, telegram=dict(cfg["telegram"],
                            text_timeout_s=min(8, remaining), media_timeout_s=min(15, remaining)))
        return deliver(post_cfg, session, message, media, send, reply_to=reply_to)

    def prepare_clip(inc, refresh=False):
        if cfg.get("_background_media"):
            # Pending video retries and website requests must use the same revision.
            refresh = bool(inc.get("clip") and (refresh or inc.get("clip_video_pending")
                           or inc.get("clip_needs_refresh") or _clip_measurement_changed(inc)))
            return mediaqueue.request_clip(cfg, inc, now, refresh=refresh)
        return add_clip(cfg, wms, S, inc, now, refresh=refresh)

    def look_back(inc):
        """NASA saw a fire at sea hours late: find when it first showed on Meteosat-12."""
        fac = inc.get("facility")
        if not (SW.get("look_back") and inc.get("place") in ("sea", "coast") and inc.get("polar")
                and not set(inc.get("srcs") or []) & seawatch.FINDER_SOURCES
                and not day_requires_confirmation(inc) and not inc.get("looked_back")
                and inc.get("priority") == "high" and not (fac and fac["km"] <= 3)):
            return False
        inc["looked_back"] = iso(now)
        try:
            spots = seawatch.look_back(cfg, wms, inc, Memory(S.memory), geo)
        except Exception as e:  # noqa: BLE001
            log.warning("look back for %s failed: %s", inc["id"], e)
            spots = []
        for sp in spots:
            add_to_incident(inc, sp)
        if spots:
            log.info("look back %s: seen on Meteosat-12 from %s (%d pictures)", inc["id"],
                     min(sp["t"] for sp in spots), len(spots))
            finalize(inc, cfg)
            inc.pop("clip", None)  # remake the clip around the real start
            inc.pop("snap", None)
            inc.pop("clip_tried", None)
        else:
            log.info("look back %s: nothing on Meteosat-12 before NASA's pass", inc["id"])
        return True
    # Refresh old presentation without removing a usable clip before its replacement exists.
    if S.state.get("clip_style") != CLIP_STYLE:
        for inc in S.incidents:
            if (inc.get("clip") or inc.get("snap")) and now - parse_time(inc["last"]) <= dt.timedelta(days=2):
                inc["clip_needs_refresh"] = True
            for k in ("clip_tried", "clip_refresh_tried"):
                inc.pop(k, None)
        S.state["clip_style"] = CLIP_STYLE
    if S.state.get("sea_check") != SEA_CHECK:
        for inc in S.incidents:
            if inc.get("srcs") and set(inc["srcs"]) <= seawatch.FINDER_SOURCES and len(inc.get("families") or []) < 2 \
                    and now - parse_time(inc["last"]) <= dt.timedelta(days=2):
                for k in ("clip", "snap", "clip_tried", "clip_note"):
                    inc.pop(k, None)
        S.state["sea_check"] = SEA_CHECK
    rejudge = S.state.get("rules") != RULES
    if rejudge:
        keep, dropped = [], 0
        steady_days = max(3, math.ceil(0.5 * min(avail, int(M["window_days"]))))
        for inc in S.incidents:
            typ, kd = inc.get("typical_frp") or 0.0, inc.get("known_days") or 0
            if inc["kind"] == "unusual":
                still = (kd >= steady_days and geo.facility_within(inc["lat"], inc["lon"],
                                                                    float(M["unusual_facility_km"]))
                         and (inc.get("max_frp") or 0) >= max(float(M["unusual_ratio"]) * typ,
                                                              typ + float(M["unusual_add_mw"])))
                if not still:
                    dropped += 1  # it was the usual heat of that place after all
                    continue
            elif avail > 0 and kd >= min(int(M["persistent_min_days"]), avail):
                dropped += 1  # a former 'unusual' one: the usual heat of that place
                continue
            if inc.get("srcs") and set(inc["srcs"]) <= seawatch.FINDER_SOURCES and inc.get("clip_note") and not inc.get("clip"):
                inc["unseen_close"] = True  # the sea finder's speck that the closer look did not show
            inc.pop("_pos", None)  # describe it again (nearest facility rules changed too)
            if inc.get("area_days") is None:
                inc["area_days"] = mem.profile(inc["lat"], inc["lon"], inc["first"][:10],
                                               int(M["window_days"]), 5)[0]
            keep.append(inc)
        S.incidents = keep
        S.state["rules"] = RULES
        log.info("re-judged stored incidents with the current rules: %d kept, %d dropped", len(keep), dropped)
    for inc in S.incidents:
        if inc.get("status") == "alerted" and "alerted_max_frp" not in inc:
            # Earlier releases did not save the sent strength. Do not invent its value
            # or flood the channel with historical increases on the first upgraded run.
            inc["alerted_max_frp"] = _sent_peak(inc)
            inc.setdefault("alert_updated_at", inc.get("alerted_at") or iso(now))
        recent = now - parse_time(inc["last"]) <= dt.timedelta(hours=24)
        stale_map = recent and (inc.get("_pos") or [None])[-1] != geo.version
        if inc["id"] not in touched and not (recent and inc.get("status") in ("pending", "alerted")) \
                and not stale_map and not rejudge:
            continue
        if inc.get("place") == "sea":  # another fire confirmed close by shortly before this one
            inc["near_confirmed"] = nearby_confirmed(inc, S.incidents)
        enrich(inc, cfg, geo)
        if look_budget > 0 and look_back(inc):
            look_budget -= 1
        if inc["priority"] != "high" or inc.get("status") == "held":
            continue
        if day_requires_confirmation(inc) or association_requires_confirmation(inc) or inc.get("recovered_historical"):
            continue  # experimental day evidence cannot send alerts or updates without corroboration
        rank = VERDICT_RANK[inc["verdict"]]
        if inc.get("alerted_verdict") is None and rank >= min_rank and not SW.get("alerts") \
                and set(inc.get("srcs") or []) <= seawatch.FINDER_SOURCES:
            continue  # the sea finder is still on trial: shown in the app, not sent, until others confirm
        if inc.get("alerted_verdict") is None and rank >= min_rank:
            if now - parse_time(inc["last"]) > max_age:
                # e.g. the first run after installing: shown in the app, not pushed as news
                inc["alerted_verdict"], inc["status"] = inc["verdict"], "late"
                log.info("not alerting %s: its newest evidence is older than %s", inc["id"], max_age)
                continue
            first_alerts.append(inc)
        elif inc.get("alerted_verdict"):
            verdict_changed = rank > VERDICT_RANK[inc["alerted_verdict"]]
            intensity_changed = intensity_update_due(inc, cfg, now)
            if inc.get("status") == "alerted" and (intensity_changed or now - parse_time(inc["last"]) <= max_age):
                if verdict_changed or intensity_changed:
                    updates.append((inc, intensity_changed))
            elif verdict_changed:
                inc["alerted_verdict"] = inc["verdict"]  # remember it quietly: the app shows it

    # safety valve: many "new" fires at once usually means the memory is not ready, not a war
    max_new = int(cfg["alerts"].get("max_new_per_run", 6))
    if len(first_alerts) > max_new:
        log.warning("%d new heat sources in one run: holding them, sending one summary", len(first_alerts))
        for inc in first_alerts:
            inc["status"], inc["alerted_verdict"] = "held", inc["verdict"]
        summary = (f"⚠️ <b>رُصدت {len(first_alerts)} مصادر حرارة جديدة دفعة واحدة</b>\n"
                   "هذا غير معتاد، وقد يكون بسبب نقص في ذاكرة الحرارة المعتادة لا بسبب حرائق حقيقية. "
                   "لم أرسلها كتنبيهات منفصلة؛ راجعها في التطبيق قبل أي نشر.")
        if cfg.get("app_url"):
            summary += f'\n<a href="{cfg["app_url"]}">افتح التطبيق</a>'
        post(summary)
        first_alerts = []

    for inc in first_alerts:
        inc["status"] = "pending"
        media = None
        ready = prepare_clip(inc)
        if ready and inc.get("clip"):
            media = Path(cfg["_site_dir"]) / inc["clip"]
        message = alert_message(inc, cfg)
        if media is None and cfg["clip"].get("enabled", True):
            message += "\n🎞 الفيديو قيد التجهيز؛ سيُرسل عند توفر صور واضحة للرصد."
        sent = post(message, media)
        if sent:
            S.sent = True
            inc["alerted_verdict"], inc["status"], inc["alerted_at"] = inc["verdict"], "alerted", iso(now)
            _remember_post(inc, now)
            inc["clip_video_pending"] = bool(cfg["clip"].get("enabled", True) and media is None)
            if sent is not True:
                inc["tg_msg"] = sent  # updates answer this post
            log.info("ALERT %s: %s (%s)", inc["id"], inc["headline"], inc["verdict"])
    updated_ids = set()
    for inc, intensity in updates:
        updated_ids.add(inc["id"])
        first_post = inc.get("tg_msg")
        media = None
        if intensity and prepare_clip(inc, refresh=True) and inc.get("clip"):
            path = Path(cfg["_site_dir"]) / inc["clip"]
            if path.exists():
                media = path
        message = alert_message(inc, cfg, update=True, threaded=bool(first_post),
                                intensity=intensity, delayed=_delayed_update(inc, cfg, now))
        if intensity and media is None and cfg["clip"].get("enabled", True):
            message += "\n🎞 الفيديو المحدث قيد التجهيز؛ سيُرسل عند توفر صور تُظهر الرصد الجديد بوضوح."
        if post(message, media, reply_to=first_post):
            S.sent = True
            inc["alerted_verdict"] = inc["verdict"]
            _remember_post(inc, now)
            if intensity:
                inc["clip_video_pending"] = bool(cfg["clip"].get("enabled", True) and media is None)
            log.info("UPDATE %s -> %s%s", inc["id"], inc["verdict"], " (stronger)" if intensity else "")

    # If the newest pictures were late, the strength update went out immediately as text.
    # Retry its media separately without repeating the same rise notification every run.
    for inc in S.incidents:
        if inc["id"] in updated_ids or not inc.get("clip_video_pending") \
                or inc.get("status") != "alerted" or inc.get("priority") != "high" \
                or day_requires_confirmation(inc) or association_requires_confirmation(inc) or inc.get("recovered_historical") \
                or now - parse_time(inc["last"]) > update_max_age:
            continue
        if not prepare_clip(inc, refresh=True) or not inc.get("clip"):
            continue
        media = Path(cfg["_site_dir"]) / inc["clip"]
        if not media.exists():
            continue
        first_post = inc.get("tg_msg")
        if post(alert_message(inc, cfg, update=True, threaded=bool(first_post),
                              video_update=True, delayed=_delayed_update(inc, cfg, now)), media,
                reply_to=first_post):
            S.sent = True
            inc["clip_video_pending"] = False
            _remember_post(inc, now)

    # sea incidents found before the look-back existed, or not touched in this run
    for inc in sorted(S.incidents, key=lambda i: i["last"], reverse=True):
        if look_budget <= 0:
            break
        if now - parse_time(inc["last"]) <= dt.timedelta(days=2) and inc.get("place") and look_back(inc):
            look_budget -= 1

    # every important incident gets its clip in the app, even when it was not sent (a few per run)
    budget = int(cfg["clip"].get("per_run", 4))
    for inc in sorted(S.incidents, key=lambda i: i["last"], reverse=True):
        important_recent = (inc.get("priority") == "high"
                            and now - parse_time(inc["last"]) <= dt.timedelta(days=2))
        if not important_recent:
            continue
        if _clip_measurement_changed(inc):
            inc["clip_needs_refresh"] = True
        # Record the pending state even when another video used this run's budget.
        if budget <= 0:
            continue
        needs_refresh = bool(inc.get("clip_needs_refresh"))
        if not inc.get("clip") or needs_refresh:
            tried = inc.get("clip_refresh_tried" if needs_refresh else "clip_tried")
            retry = float(cfg["clip"].get("refresh_retry_min", 10)) if needs_refresh else 60
            if not tried or now - parse_time(tried) >= dt.timedelta(minutes=retry):
                prepare_clip(inc, refresh=needs_refresh)
                budget -= 1

    # 5) tidy up and publish
    keep = dt.timedelta(days=int(I["keep_days"]))
    S.incidents = [i for i in S.incidents if now - parse_time(i["last"]) <= keep]
    mem.prune(today, int(M["window_days"]) + 5)
    S.state["slots"] = {k: v for k, v in slots.items()
                        if now - parse_time(k.rsplit("|", 1)[1]) <= dt.timedelta(hours=6)}
    S.state["seen"] = {k: v for k, v in S.state.get("seen", {}).items()
                       if now - parse_time(v) <= dt.timedelta(days=3)}
    for cache in ("geocode", "landsea"):
        c = S.state.get(cache, {})
        if len(c) > 5000:
            S.state[cache] = dict(list(c.items())[-4000:])
    days = S.state.setdefault("run_days", [])
    if today not in days:
        days.append(today)
    S.state["run_days"] = sorted(days)[-60:]
    finished = utcnow()
    elapsed = max(0.0, time.monotonic() - started_clock)
    S.state["last_run"] = iso(finished)
    S.state["scan_started"] = iso(now)
    S.save()
    publish(cfg, S, status, now, learning, len(dets), finished=finished, duration=elapsed)
    return S


PUBLIC = ["id", "kind", "headline", "where", "country", "place", "facility", "start_text", "last_text",
          "first", "last", "lat", "lon", "best", "size", "max_frp", "verdict", "reason", "priority",
          "sources_text", "regions", "region_name", "clip", "snap", "clip_note", "frp_series",
          "clip_video_pending", "clip_needs_refresh", "clip_updated_at", "clip_max_frp",
          "recovered_historical", "association_uncertain", "association", "correction_note"]


def publish(cfg, S, status, now, learning, n_dets, *, finished=None, duration=None):
    site = Path(cfg["_site_dir"])
    finished = finished or utcnow()
    partial = any(row.get("pending_recent", 0) or row.get("failed", 0) or row.get("deferred", 0)
                  or (not row.get("ok", False) and key != "recovery"
                      and not row.get("experimental"))
                  for key, row in status.items() if key != "recovery")
    items = [{k: i.get(k) for k in PUBLIC} for i in S.incidents if i.get("headline")]
    for item in items:
        if item.get("clip_video_pending") or item.get("clip_needs_refresh"):
            item["clip_status_text"] = "يجري تجهيز فيديو محدث وفق القياسات الجديدة"
        elif item.get("clip"):
            item["clip_status_text"] = ""
        elif not cfg["clip"].get("enabled", True):
            item["clip_status_text"] = "إنشاء الفيديو غير مفعّل حالياً"
        elif item.get("clip_note"):
            item["clip_status_text"] = item["clip_note"]
        elif item.get("priority") == "high" and now - parse_time(item["last"]) <= dt.timedelta(days=2):
            item["clip_status_text"] = "الفيديو قيد التجهيز؛ سيظهر عند توفر صور صالحة"
        else:
            item["clip_status_text"] = "لا يتوفر فيديو لهذا الحدث حالياً"
    items.sort(key=lambda i: i["last"], reverse=True)
    # every important incident, but only the latest small ones, so the app stays light on a phone
    low = [i for i in items if i["priority"] != "high"][250:]
    if low:
        drop = {id(i) for i in low}
        items = [i for i in items if id(i) not in drop]
    save_json(site / "data" / "incidents.json", {"generated": iso(finished), "items": items}, compact=True)
    save_json(site / "data" / "meta.json", {
        "generated": iso(finished), "scan_started": iso(now), "scan_finished": iso(finished),
        "scan_duration_s": round(duration if duration is not None else max(0, (finished - now).total_seconds()), 1),
        "scan_partial": bool(partial), "app_name": cfg["app_name"], "learning": learning,
        "detections_this_run": n_dets, "local_offset_hours": cfg["local_time"]["offset_hours"],
        "local_name": cfg["local_time"]["name"],
        "regions": [{"id": r["id"], "name": r["name"], "bbox": r["bbox"]} for r in cfg["regions"]],
        "sources": status, "telegram": bool(cfg["telegram"].get("bot_token"))})
    # remove clips of incidents that are gone
    keep = {i.get("clip") for i in items} | {i.get("snap") for i in items}
    clips = site / "clips"
    if clips.exists():
        for f in clips.iterdir():
            if f"clips/{f.name}" not in keep:
                f.unlink()
