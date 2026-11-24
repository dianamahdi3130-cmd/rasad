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
                    nearby_confirmed, new_incident, day_requires_confirmation, association_requires_confirmation,
                    sea_finder_doubt, video_note)
from .clip import NotVisible, make_clip
from .geodb import Geo
from . import seawatch
from . import backfill, geocollect, mediaqueue, earlyalerts
from . import acquisitions, archive, assess, flares, noaafire, patterns, repairs, satpic, smoke
from .notify import deliver, telegram_ready, tg_send
from .sources import detect_geo_layer, fetch_frp, firms_fetch, region_tiles, union_bbox
from .updates import intensity_update_due, peak_reading
from .util import (floor_time, haversine_km, in_bbox, iso, load_json, log, make_session, official_reads,
                   official_reads_done, parse_time, save_json, utcnow)
from .wms import WMS, Availability, latest_time

STALE_HOURS = 3
RULES = 21  # bump when the importance rules change, so stored incidents are judged again
CLIP_STYLE = 19  # bump when clips change look, so the app's clips are made again
SEA_CHECK = 1    # bump when the closer look changes: clips of the sea finder's own finds are checked again
TIDY = 1         # bump to tidy the stored incidents once more (copies under one id, split recoveries)
PATTERNS = 3     # bump when the heat patterns' rules change, so every stored event is labelled again
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
               # a confirmed fire where there was no heat for a month, in the dry countries of
               # the Gulf, is news even far from every mapped facility (brain.new_heat)
               "new_heat_countries": ["السعودية", "الإمارات", "قطر", "البحرين", "الكويت", "عمان", "عُمان"],
               "new_heat_min_mw": 5, "new_heat_min_pictures": 4, "new_heat_strong_mw": 20,
               # ...and heat that comes back to a place quiet for a week and burns on (patterns.new_heat_path)
               "new_heat_sustained": True, "new_heat_quiet_days": 7, "new_heat_max_known_days": 2,
               "new_heat_max_area_days": 1, "new_heat_sustained_min_mw": 10, "new_heat_sustained_hours": 6,
               "new_heat_sustained_pictures": 12, "new_heat_tagged_hours": 3, "new_heat_max_gap_h": 3.5,
               # NASA's daytime pixels on a mapped solar plant's panels, Meteosat cold: not posted
               "solar_glint_stop": True, "solar_glint_km": 3, "solar_glint_min_mw": 50,
               "intensity_ratio": 1.5, "intensity_add_mw": 1, "intensity_interval_min": 30,
               "intensity_max_age_hours": 24,
               "focus_countries": ["العراق", "السعودية", "الإمارات", "قطر", "البحرين", "الكويت", "عمان",
                                   "عُمان", "إيران", "اليمن"]},
    "memory": {"persistent_min_days": 2, "window_days": 30, "unusual_ratio": 5, "unusual_add_mw": 25,
               "unusual_facility_km": 3,
               # a plant's flare is news only when it burns well above its own habit (a level it
               # reached on two different days of the month): the same plants surge to the same
               # level every few days, and that is their normal working, not a fire (16 of the
               # first 25 posts were such surges). One episode is posted once: not again within
               # unusual_repeat_days unless unusual_repeat_ratio times what was posted.
               "unusual_record_ratio": 2.0, "unusual_repeat_days": 3, "unusual_repeat_ratio": 1.5},
    "incidents": {"merge_km": 6, "merge_hours": 12, "keep_days": 7,
                  "max_polar_gap_hours": 3, "polar_relocation_km": 2},
    "firms": {"map_key": "", "base_url": "https://firms.modaps.eosdis.nasa.gov",
              "sources": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"],
              "bootstrap_days": 10, "max_day_range": 5, "live_hours": 36,
              "budget_s": 25, "request_timeout_s": 20, "max_workers": 2},
    # NOAA's own files of NOAA-20/21 (public AWS buckets, no account): noaafire.py; on in config.yaml
    "noaa": {"enabled": False, "budget_s": 12, "request_timeout_s": 10, "workers": 4, "max_age_hours": 6},
    # heat patterns (patterns.py): a label for heat at or near energy sites, shown in the app only;
    # never posted and never part of a verdict until reviewed with the owner (docs/plans/heat_patterns.md)
    "patterns": {"enabled": True, "scope_facility_km": 10, "solar_km": 3,
                 "wide_min_px": 8, "wide_min_km": 1.5, "wide_min_mw": 50, "wide_usual_ratio": 3,
                 "second_look_h": 3, "footprint_learn_days": 7,
                 "link_h": 3, "link_min_km": 5, "link_max_km": 500},
    # «مؤشرات تستحق الانتباه» (patterns.signals_of): app only, never a cause
    "signals": {"enabled": True, "spot_km": 0.5, "rare_share": 0.02, "rare_recent_days": 2, "usual_recent_days": 10,
                "usual_share": 0.10, "flare_near_km": 5, "facility_km": 3, "sudden_mw": 20, "burn_min_ha": 0.5,
                "min_atlas_dates": 50, "spot_record_km": 5, "odd_min_mw": 5},
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
    # The map is asked for tile by tile, each part by itself; a part that fails twice is asked as its
    # four quarters, down to min_part_deg (on 16 Nov 2026 the public servers took 40 s or more per
    # request and timed out on others, whatever the box: geodb.build_geodata).
    "geodata": {"facility_max_km": 30, "place_max_km": 80, "coast_km": 1.5, "query_tile_deg": 8,
                "oil_query_split": 1, "min_part_deg": 1.0, "rebuild_days": 7,
                # facilities added by hand: {lat, lon, name, category, code} (config.yaml)
                "extra_facilities": [],
                "overpass_urls": ["https://overpass-api.de/api/interpreter",
                                  "https://overpass.kumi.systems/api/interpreter"],
                "nominatim_url": "https://nominatim.openstreetmap.org/reverse"},
    "clip": {"enabled": True, "frames": 8, "frames_after": 2, "half_size_km": 20, "view_zoom": 2, "px_km": 0.25,
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
    # The before/after Sentinel-2 picture sent under an alert once a clear pass comes (satpic.py).
    "satpic": {"enabled": True, "max_days": 12, "retry_hours": 3, "half_km": 2, "approx_half_km": 3,
               "lookback_days": 75, "job_budget_s": 170, "sea": False},
    # Places watched whether or not anything burns there (satpic.py): add them in config.yaml.
    "watch": {"enabled": True, "places": [], "check_hours": 3, "min_patch_ha": 0.5, "lookback_days": 45,
              "half_km": 2, "job_budget_s": 170},
    # Smoke as a second witness, silent for now (smoke.py holds the rules and their numbers).
    "smoke": {"enabled": True},
    # A plant's flare far busier than usual, silent for now (flares.py holds the rule and numbers).
    "flares": {"enabled": True},
    # Own image finders: nighttime alerts and experimental daytime candidates for review.
    "seawatch": {
        "enabled": True, "alerts": True, "look_back": True, "look_back_km_per_px": 0.25,
        # What the finder alone found is posted only once its closer look at the pictures shows
        # it (the alert then carries the video); what the closer look does not show stays in the
        # app. Without an answer within this many minutes the alert goes out as text, as before.
        "closer_look_first": True, "closer_look_wait_min": 15,
        "early_alerts": {"enabled": True, "max_age_min": 45, "max_new_per_run": 3,
                         "match_km": 1.5, "match_minutes": 40, "keep_days": 7,
                         "video_max_age_hours": 24,
                         # a first picture alone goes to the channel only when it is unmistakable
                         # (a strong rise with a flame's glow); a fainter speck waits for the
                         # second picture and then goes out as a normal alert
                         "strong_only": True, "platform_min_rise": 30},
        "layer": "mtg_fd:rgb_firetemperature", "check_layer": "mtg_fd:ir105_hrfi",
        "label": "Meteosat-12 Fire Temperature",
        # 0.25 km per pixel: the server answers anything coarser than ~0.3 km from a blurred copy
        # that is also one picture behind, in which a small fire all but disappears
        # (docs/reviews/hormuz_ras_al_khaimah_20261114.md)
        "cadence_min": 10, "delay_min": 0, "slots_per_run": 6, "km_per_px": 0.25,
        # read first in every check, every picture of theirs (never skipped when the time runs out)
        "priority_boxes": ["hormuz_oman", "gulf_south", "gulf_north"],
        # slots missed in the last hours (the source late or down) are checked later, newest first,
        # within this many seconds per run
        "catchup_hours": 3, "budget_s": 240,
        # Levels (0-255) of the picture's red colour (3.8 micron), never Kelvin or MW. Compared
        # with the middle of the pictures 30, 60 and 90 minutes before. A speck needs a rise of 7,
        # or of 4 together with a new glow of 2 in the short-wave colours (flames only, at night).
        # Tuned on the real nights of 1, 9, 11, 13 and 14 November 2026: every real fire kept,
        # no false pair on the cloudy and the flare-rich nights.
        "ref_minutes": [30, 60, 90], "min_rise": 7.0, "min_z": 5.0, "flame_rise": 4.0, "flame_glow": 2.0,
        "max_area_km2": 60, "min_area_px": 4, "max_was_hot": 12.0,
        # low cloud or fog drifting off is not a fire: the speck must be hotter than the clear sea
        # around it, and its warming no longer than a few km (a cloud edge runs on for tens of km;
        # the first picture of the real fire of 1 November spread 8.5 km at full detail)
        "min_above_sea": 3.0, "max_warm_extent_km": 12.0,
        # the sea around a fire is calm; where clouds drift, pixels for kilometres around change.
        # Share of sea pixels 3-15 km away that changed by 3 levels: real fires 0.00-0.03, cloud
        # specks 0.05-0.9. A blaze (rise 30 with a glow of 10) is believed in a restless sky too.
        "max_restless": 0.05, "max_restless_strong": 0.30, "strong_rise": 30.0, "strong_glow": 10.0,
        # the glow around a big flare brightens and fades with it: not a fire of its own
        "halo_km": 10.0, "halo_margin": 30.0,
        # what a single picture must show to be told at once
        "early_min_rise": 15.0, "early_min_glow": 4.0,
        "min_coast_km": 3.0, "platform_km": 3.0, "known_heat_km": 10.0, "max_z_ir105": 6.0,
        "known_heat_min_days": 3, "known_heat_exclusion_km": 3.0,
        "prefetch": {"enabled": True, "budget_s": 90, "request_timeout_s": 40, "batch_size": 4},
        "sparkle_min_days": 3,
        "sun_max_elev": -6.0,
        # These thresholds are image display levels, never Kelvin or fire power in MW.
        # Day candidates need independent official/NASA evidence before any Telegram alert.
        "daytime": {
            "enabled": True, "ref_minutes": [30, 60],
            # the experimental day finder keeps the picture detail it was tuned on (its many
            # sunlit-cloud tests would have to be measured again on full-detail pictures)
            "km_per_px": 0.5,
            # from first light (where the night finder stops), not from full daylight: dawn and
            # dusk used to be watched by neither finder, about two hours around each sunrise
            "min_sun_elev": -6.0, "min_view_elev": 10.0, "min_glint_angle": 25.0,
            "min_rise": 12.0, "min_z": 8.0, "min_above_sea": 12.0, "max_was_hot": 8.0,
            "max_area_km2": 40.0, "max_warm_extent_km": 8.0,
            "max_blue_contrast": 12.0, "max_blue_rise": 10.0,
            "max_ir_change_z": 3.0, "max_ir_cold_excess": 20.0,
            "max_candidates": 40, "burst_max": 3,
            "validation_grid": 8,
        },
        # Seas watched at night. Each was switched on after the finder was replayed over real
        # nights there at full detail with no false pair: Hormuz/Gulf of Oman and the southern Gulf
        # on five nights (1-14 Nov 2026); the northern Gulf (whose oil fields used to sparkle all
        # night in the blurred picture), the Arabian Sea and the Red Sea on the evening of 14 Nov.
        # "day": False keeps the experimental day finder (six pictures per slot) off a box.
        "boxes": [
            {"id": "hormuz_oman", "bbox": [55.0, 22.4, 60.2, 27.2]},
            {"id": "gulf_south", "bbox": [50.5, 23.8, 55.2, 27.8]},
            {"id": "gulf_north", "bbox": [47.8, 27.8, 51.5, 30.4], "day": False},
            # the Red Sea in pieces that follow its water (a box is read whole: less desert, less memory)
            {"id": "red_sea_bab", "bbox": [39.2, 12.0, 44.5, 16.4], "day": False},      # Bab al-Mandab
            {"id": "red_sea_south", "bbox": [38.0, 16.2, 42.8, 20.5], "day": False},
            {"id": "red_sea_middle", "bbox": [35.5, 20.0, 40.4, 23.9], "day": False},
            {"id": "red_sea_upper", "bbox": [34.0, 23.7, 38.4, 26.6], "day": False},
            {"id": "red_sea_north", "bbox": [32.3, 26.5, 36.0, 30.0], "day": False},    # Suez and Aqaba
            {"id": "arabian_west", "bbox": [52.5, 16.4, 56.2, 18.0], "day": False},
            {"id": "arabian_east", "bbox": [56.1, 16.4, 60.2, 22.5], "day": False},
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
    for key in ("known_days", "typical_frp", "area_days", "quiet_days", "usual_px", "solar_km", "memory_days", "wide"):
        if keep.get(key) is None and other.get(key) is not None:
            keep[key] = other[key]
    patterns.merge_passes(keep, other)
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


def too_old_for_first_alert(inc, now, max_age, by_change=False):
    """A first alert is news while the newest evidence is young. An incident that became important
    only because the rules or the facility map changed must also have *started* recently: a heat
    source that has burnt all day is not announced the moment the map learns its facility (it is
    shown in the app as it was)."""
    if now - parse_time(inc["last"]) > max_age:
        return True
    return bool(by_change) and now - parse_time(inc["first"]) > max_age


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

    if inc.get("preliminary") is True:
        # A first sighting gets the same video as every alert (owner, 17 Nov 2026); its own close-up
        # of the finder's picture is kept for when the usual video cannot show the speck
        try:
            return clip_for(cfg, wms, S, dict(inc, preliminary=False), focus_time)
        except Exception as e:  # noqa: BLE001
            log.info("first sighting %s: the usual video could not show it (%s); its own close-up", inc.get("id"), e)
            from .earlyclip import render
            return render(cfg, wms, inc, utcnow())

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
        start = floor_time(parse_time(inc["first"]), cad)
        # A source that stopped publishing can still show a fire it has the pictures of: old
        # pictures are refused only when they end before the fire began. (15 Nov 2026: the
        # European server's newest picture was of 18:50 UTC; an alert sent three hours later
        # about a fire of 17:40 got no video, though every picture of it was there.)
        stale = latest is not None and utcnow() - latest > dt.timedelta(hours=STALE_HOURS)
        if latest is None or (stale and latest < start):
            err = f"{L['name']}: no recent pictures"
            continue
        if focus is not None and latest < floor_time(focus, cad):
            err = f"{L['name']}: pictures of the new measurement are not ready"
            continue
        C = cfg["clip"]
        after = int(C.get("frames_after", 2))
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
        inc.pop("early_clip", None)
        inc.pop("early_clip_time", None)
        inc.pop("clip_needs_refresh", None)
        inc.pop("clip_note", None)
        if inc.pop("unseen_close", None):
            finalize(inc, cfg)
        return True
    except NotVisible:
        if inc.get("preliminary") is True:
            inc["clip_note"] = "لم تتوفر صور صالحة لإظهار البقعة الأولية بوضوح؛ ستُعاد المحاولة"
            return False
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


def usual_heat(known, steady, at_sea):
    """Heat Rasad has seen at a place before is its usual heat, not news. At sea the only usual heat
    is a platform's flare, which burns on most days; a strait where ships burned on a few days of
    the month is not a flare. On 23 Nov 2026 six earlier days of ship fires off Khasab made the
    night's ship fire there «usual»: seen in 13 pictures in a row, it never got past a first sighting."""
    return known and (steady or not at_sea)


def awaited_videos(S):
    """Media jobs a post in the channel is waiting for: an alert sent before its video, the sea
    finder's closer look (its alert waits for it), and a first sighting posted before its video."""
    out = []
    for inc in S.incidents:
        token = inc.get("media_job")
        if token and ((inc.get("status") == "alerted" and inc.get("clip_video_pending"))
                      or (inc.get("closer_look_asked") and inc.get("status") == "pending" and not inc.get("clip"))):
            out.append(token)
    for record in (S.state.get("early_night") or {}).values():
        token = (record.get("media_snapshot") or {}).get("media_job")
        if token and record.get("sent_at") and record.get("video_pending"):
            out.append(token)
    return out


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
        flares.take_videos(S.state, cfg, now, mediaqueue.import_result)   # the app's flare markers
    geo = Geo(cfg, session, wms, S.state)
    if cfg.get("_background_media"):
        method = getattr(type(wms), "budget", None)
        with wms.budget(8, timeout_s=4) if callable(method) else nullcontext():
            geo.self_test()
    else:
        geo.self_test()
    E, M, I = cfg["eumetview"], cfg["memory"], cfg["incidents"]
    FL = {**flares.DEFAULTS, **cfg.get("flares", {})}
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

    if send and telegram_ready(cfg) and not S.state.get("hello_sent"):
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
    # the picture worker waits while the official products are read (util.official_reads)
    official_reads(cfg, float(E.get("scan_budget_s", 65)) + 10)
    try:
        dets, status, n_images = geocollect.collect(
            cfg, wms, S.state, now, tiles, avail, detect=detect_geo_layer, latest=latest_time)
    finally:
        official_reads_done(cfg)

    # 1b) Own sea finders: nighttime checks and separately guarded daytime candidates.
    SW = cfg["seawatch"]
    if SW.get("enabled"):
        t1 = time.time()
        try:
            watch_cfg = cfg
            if cfg.get("_background_media"):
                # Ten sea boxes at full detail: about 30 s of arithmetic for one picture time. The
                # newest picture time comes first and always fits; an older one waiting to be caught
                # up gets what is left. (With 75 s, live on 15 Nov 2026, every check of the first
                # hours took two minutes, and the alerts of that check waited for it.)
                watch_cfg = dict(cfg, seawatch=dict(SW, budget_s=min(float(SW.get("budget_s", 240)), 45),
                                                  slots_per_run=min(int(SW["slots_per_run"]), 2)))
            seconds = float(watch_cfg["seawatch"].get("budget_s", 240))
            # In the watching loop a separate worker fetches the pictures all the time; the check
            # reads what is already there and spends only a little of its own time on the network
            # (measured live on 15 Nov 2026: left alone it used its whole budget downloading).
            network = min(seconds, float(SW.get("check_network_s", 25))) if cfg.get("_background_media") else seconds
            budget = getattr(type(wms), "budget", None)
            with wms.budget(network, timeout_s=20) if callable(budget) else nullcontext():
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
            # At night (no daylit picture left to read) the finder rests and is not late.
            resting = day_status.get("daylight") is False
            status["seawatch_day"] = {
                **day_status, "name": "كاشف رصد النهاري (Meteosat-12)",
                "ok": bool(ok and (fresh_day or resting) and not day_status.get("missing")),
                "latest": checked_at, "experimental": True,
            }
        log.info("sea watch: %d spot(s), %.0f s", len(spots), time.time() - t1)

    # Queue first-frame media while the remaining sources are being read. The
    # renderer reuses the original detection grid and never waits for a future pass.
    if cfg.get("_background_media"):
        earlyalerts.sync(cfg, S, now)
        earlyalerts.prepare(cfg, S, now, mediaqueue.request_clip)

    # 2) NASA's polar satellites. First NOAA's own files for NOAA-20/21 (noaafire.py): the same pixels
    #    FIRMS gives, about 25 minutes after a day pass and 45 after a night pass instead of about three
    #    hours. A FIRMS row that brings one of them again later is its twin, not a second sighting.
    seen = S.state.setdefault("seen", {})
    noaa_keys = S.state.setdefault("noaa_seen", {})
    twins = noaafire.Twins(seen, noaa_keys)
    confirmed_by_firms = set()          # NOAA pixels whose FIRMS copy came in this check
    if cfg["noaa"].get("enabled"):
        noaa_status = {}
        try:
            fresh = noaafire.collect(cfg, session, S.state, now, box, status=noaa_status)
        except Exception as e:  # noqa: BLE001 - an optional early source must never stop a check
            log.warning("NOAA files failed: %s", e)
            fresh, noaa_status = [], {**noaa_status, "ok": False}
        n_noaa = n_twin = 0
        for d in fresh:
            k = f"{d['src']}|{d['t']}|{d['lat']}|{d['lon']}"
            if k in noaa_keys or k in seen:
                continue
            if twins.absorbs(d):
                n_twin += 1
                continue
            noaa_keys[k] = 0               # until its FIRMS copy comes (kept two days, see the end)
            twins.add(k, d, noaa=True)
            n_noaa += 1
            dets.append(d)                 # keeps via='noaa' on its reading until FIRMS has it too
        log.info("NOAA files: %d files read, %d over the regions, %d new pixels, %d already from FIRMS",
                 noaa_status.get("read", 0), noaa_status.get("over", 0), n_noaa, n_twin)
        status["noaa"] = {**noaa_status, "name": "أقمار ناسا من ملفات NOAA المباشرة (VIIRS)",
                          "detections": n_noaa}

    # 3) NASA (VIIRS/MODIS) through FIRMS, precise but about three hours late; MODIS only comes this way.
    #    Ask for today AND yesterday (UTC): the night passes over the Gulf (about 21:30-23:30 UTC)
    #    are published after midnight UTC, so "today only" would never see them.
    if cfg["firms"]["map_key"]:
        nasa_status = {}
        nasa_cfg = dict(cfg, firms=dict(cfg["firms"], source_offset=int(S.state.get("firms_source_offset", 0))))
        polar = firms_fetch(session, nasa_cfg, box, 2, status=nasa_status)
        S.state["firms_source_offset"] = (int(S.state.get("firms_source_offset", 0)) + 2) % max(1, len(cfg["firms"]["sources"]))
        live_from = now - dt.timedelta(hours=float(cfg["firms"].get("live_hours", 36)))
        n_new = n_old = 0
        new_rows = []
        for d in polar:
            k = f"{d['src']}|{d['t']}|{d['lat']}|{d['lon']}"
            if k in seen:
                continue
            seen[k] = d["t"]
            new_rows.append(d)
        pairs = twins.match(new_rows)  # read from NOAA's file already: the same sightings
        confirmed_by_firms.update(pairs.values())
        n_twin = len(pairs)
        for i, d in enumerate(new_rows):
            if i in pairs:
                continue
            if parse_time(d["t"]) < live_from:
                d["_history"] = True  # too old to report: only remembered as heat history
                n_old += 1
            else:
                n_new += 1
            dets.append(d)
        log.info("NASA: %d detections in the last 2 days, %d new to judge, %d kept as history, %d already from NOAA's files",
                 len(polar), n_new, n_old, n_twin)
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
    # the strength of the fire symbols is read from the same server: the worker waits again, until
    # the judgement below has read what it needs (the note ends by itself after that time)
    official_reads(cfg, float(E.get("strength_retry_budget_s", 10)) + float(E.get("strength_budget_s", 20)) + 5)
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
    PT = cfg.get("patterns") or {}
    SG = cfg.get("signals") or {}
    # Decide the association once for all connected pixels of one pass. A CSV's
    # row order must not split a pass or let its last pixel join another incident.
    grouped = acquisitions.groups(
        [d for d in dets if any(in_bbox(r["bbox"], d["lat"], d["lon"]) for r in regions)], merge_km)
    membership = {id(det): group for group in grouped for det in group}
    dets = [det for group in grouped for det in group]
    patterns.record_footprints(S.state, dets, today)
    group_matches = {}
    # A detection is matched against incidents near its own time. Most are fresh (the last
    # hours), but a late picture, a recovered past slot or a strength read later can be up to
    # days old: those look at the whole memory, so they join their incident instead of
    # starting a copy of it.
    fresh_from = now - dt.timedelta(hours=merge_h)
    open_incs = [i for i in S.incidents if parse_time(i["last"]) >= fresh_from - dt.timedelta(hours=merge_h)]
    by_id = {i["id"]: i for i in S.incidents}

    def pattern_facts(inc, det, reach):
        """What the heat patterns need from this moment (patterns.py, silent): how long the place
        was quiet, its usual footprint, the nearest solar plant, and whether Meteosat was read here
        around the first sighting (the readings are kept only six hours)."""
        try:
            inc["quiet_days"] = patterns.quiet_days(mem, det["lat"], det["lon"], det["t"][:10],
                                                    int(M["window_days"]), reach)
            inc["memory_days"] = min(avail, int(M["window_days"]))   # what 'no heat for N days' may say
            if patterns.near_earlier_heat(S.incidents, inc, det["lat"], det["lon"], det["t"], reach):
                inc["quiet_days"] = 0   # heat here earlier the same day: not a quiet place
            inc["usual_px"] = patterns.usual_pixels(S.state, det["lat"], det["lon"], det["t"][:10],
                                                    int(M["window_days"]), int(PT.get("footprint_learn_days", 7)))
            solar = geo.solar_km(det["lat"], det["lon"], float(PT.get("solar_km", 3)))
            inc["solar_km"] = round(float(solar), 2) if isinstance(solar, (int, float)) else None
            patterns.onset_facts(inc, det, slots)
        except Exception as e:  # noqa: BLE001 - a silent label must never stop a check
            log.warning("heat pattern facts for %s: %s", inc.get("id"), e)

    def candidates(det):
        return open_incs if parse_time(det["t"]) >= fresh_from else S.incidents

    touched, frp_budget = set(), 40
    # A pixel read from NOAA's file that FIRMS has now brought too: its reading loses the «NOAA only»
    # mark (it now counts as a look at a faint sea pixel, and NASA's map shows it) and is judged again.
    if confirmed_by_firms:
        for inc in S.incidents:
            for row in inc.get("polar") or []:
                if row.get("via") == "noaa" and f"{row['src']}|{row['t']}|{row['lat']}|{row['lon']}" in confirmed_by_firms:
                    row.pop("via", None)
                    touched.add(inc["id"])
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
        # The plant's own habit: a level it reached on at least two different earlier days of the
        # month. A new "unusual heat" event must clearly beat it. (Until 15 Nov 2026 the bar was
        # the single highest reading, today's earlier ones included: a fire that grew through the
        # day, or a plant climbing day after day, silenced itself. Ahmadi went 49, 66, 85, 118 MW
        # on four days against a usual 2 MW without a word.)
        site_habit, site_record = mem.habit(d["lat"], d["lon"], day, int(M["window_days"]), reach) if unusual else (None, None)
        beats_record = site_habit is None or frp >= float(M.get("unusual_record_ratio", 2.0)) * site_habit
        if unusual and beats_record:
            # ...and one episode is one piece of news: the same plant is not posted again within
            # a few days unless it now burns half as hard again as what was posted
            again = float(M.get("unusual_repeat_ratio", 1.5))
            since = parse_time(d["t"]) - dt.timedelta(days=float(M.get("unusual_repeat_days", 3)))
            for other in S.incidents:
                if other.get("kind") == "unusual" and other.get("alerted_verdict") \
                        and parse_time(other["last"]) >= since \
                        and haversine_km(d["lat"], d["lon"], other["lat"], other["lon"]) <= 2.0 * reach + 2.0 \
                        and frp < again * float(other.get("max_frp") or 0.0):
                    beats_record = False
                    break
        routine = usual_heat(known, steady, known and not steady and geo.place_kind(d["lat"], d["lon"]) == "sea") and not unusual
        # a steady flare burning far busier than usual: noted for the app only (flares.py)
        if FL.get("enabled", True) and d["src"] == flares.SRC and steady and typical is not None \
                and (steady_facility if steady_facility is not None else
                     geo.facility_within(d["lat"], d["lon"], float(M["unusual_facility_km"]))):
            flares.observe(S.state, d, typical, geo.facility(d["lat"], d["lon"]), FL)
        if not experimental_day:
            mem.record(d["lat"], d["lon"], day, frp)
        if d["kind"] == "polar" and str(d.get("src", "")).startswith("VIIRS") and SG.get("enabled", True):
            # which ~500 m spot of a site burns: Rasad's own record of NASA's spots, beside the long-term map
            try:
                A = patterns.atlas()
                if geo.facility_within(d["lat"], d["lon"], float(SG.get("spot_record_km", 5))) is True or (
                        A and patterns.usual_flare_km(A, d["lat"], d["lon"], float(SG.get("flare_near_km", 5)),
                                                      float(SG.get("usual_share", 0.10))) is not None):
                    patterns.record_spot(S.state, d, day)
                    d["_spot"] = True            # its earlier days are counted once its event is known
            except Exception as e:  # noqa: BLE001 - a silent signal must never stop a check
                log.warning("heat spots: %s", e)
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
        if inc is None and unusual and not beats_record:
            continue  # within the plant's habit, or the episode already posted: its flare, not news
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
                if unusual and site_record is not None:
                    inc["site_record"] = round(float(site_record), 1)
                if unusual and site_habit is not None:
                    inc["site_habit"] = round(float(site_habit), 1)
                # days with heat within ~10 km: farmland burnt every season and flare fields are busy
                inc["area_days"] = mem.profile(d["lat"], d["lon"], day, int(M["window_days"]), 5)[0]
                pattern_facts(inc, d, reach)
                S.incidents.append(inc)
                open_incs.append(inc)
                by_id[inc["id"]] = inc
            if d["kind"] == "polar":
                group_matches[group_id] = inc
        if d.get("_no_alert"):
            inc["recovered_historical"] = True
        elif not d.get("_recovered") and now - parse_time(d["t"]) <= dt.timedelta(hours=8):
            inc.pop("recovered_historical", None)
        if d.pop("_spot", None):
            try:   # days with heat at this spot before this event began (its own days never count)
                d["spot"] = patterns.spot_days(S.state, d["lat"], d["lon"], min(inc["first"], d["t"])[:10],
                                               float(SG.get("spot_km", 0.5)))
            except Exception as e:  # noqa: BLE001
                log.warning("heat spots: %s", e)
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

    def waits_for_closer_look(inc):
        """Found by Rasad's own sea finder alone: its closer look speaks before the channel hears.
        Two of its first three alerts were doubted by that very look a minute after they were
        posted (the last one: a faint line off Taqah, Oman, 16 Nov 2026). Shown by the pictures,
        it goes out with its video; not shown, it is doubted and stays in the app."""
        if not SW.get("closer_look_first", True) or not cfg["clip"].get("enabled", True):
            return False
        srcs = set(inc.get("srcs") or [])
        if not srcs or not srcs <= seawatch.FINDER_SOURCES or inc.get("early_ids") or inc.get("clip"):
            return False   # others saw it too, or it was told at once as unmistakable, or the look showed it
        asked = inc.setdefault("closer_look_asked", iso(now))
        prepare_clip(inc)
        if inc.get("clip"):
            return False                   # made on the spot (one process): out with its video
        if sea_finder_doubt(inc):
            return True                    # the look just said no: the incident is doubted now
        if now - parse_time(asked) >= dt.timedelta(minutes=float(SW.get("closer_look_wait_min", 15))):
            return False                   # no answer in time (a slow source): out as text, as before
        inc["status"] = "pending"          # looked at again in the next checks, sighting or not
        return True

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
    # how important each stored incident was before this check changed anything (see below)
    was_priority = {inc["id"]: inc.get("priority") for inc in S.incidents}
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
    # heat patterns: events made before them learn how long their place had been quiet (once)
    for inc in S.incidents:
        if "quiet_days" not in inc and inc.get("place") == "land" \
                and now - parse_time(inc["last"]) <= dt.timedelta(days=3):
            first = min(inc.get("observations") or [{}], key=lambda o: o.get("t") or "9")
            reach = {"mtg": 3, "msg_iodc": 2}.get(first.get("family"), 2) if first.get("kind") == "geo" else 1
            inc["quiet_days"] = patterns.quiet_days(mem, inc["lat"], inc["lon"], inc["first"][:10],
                                                    int(M["window_days"]), reach)
            inc["memory_days"] = min(mem.days_known(inc["first"][:10], int(M["window_days"])), int(M["window_days"]))
            if patterns.near_earlier_heat(S.incidents, inc, inc["lat"], inc["lon"], inc["first"], reach):
                inc["quiet_days"] = 0
    if S.state.get("patterns") != PATTERNS:   # once: every stored event gets (or renews) its label
        try:
            patterns.label_stored(S.incidents, cfg)
            S.state["patterns"] = PATTERNS
        except Exception as e:  # noqa: BLE001 - a silent label must never stop a check
            log.warning("heat patterns: labelling stored events failed: %s", e)
    # Transfer only an exact originating observation to a genuine incident, so
    # confirmation replies to its early post without merging a different nearby fire.
    earlyalerts.sync(cfg, S, now)
    for inc in S.incidents:
        if inc.get("status") == "alerted" and "alerted_max_frp" not in inc:
            # Earlier releases did not save the sent strength. Do not invent its value
            # or flood the channel with historical increases on the first upgraded run.
            inc["alerted_max_frp"] = _sent_peak(inc)
            inc.setdefault("alert_updated_at", inc.get("alerted_at") or iso(now))
        recent = now - parse_time(inc["last"]) <= dt.timedelta(hours=24)
        old_map = (inc.get("_pos") or [None])[-1]  # the facility map it was last described with
        stale_map = recent and old_map != geo.version
        if inc["id"] not in touched and not (recent and inc.get("status") in ("pending", "alerted")) \
                and not stale_map and not rejudge:
            continue
        if inc.get("place") == "sea":  # another fire confirmed close by shortly before this one
            inc["near_confirmed"] = nearby_confirmed(inc, S.incidents)
        enrich(inc, cfg, geo)
        # important only because the rules or the facility map changed, not because of what it did
        # (an incident whose place was never described yet is not such a case: nothing changed for it)
        by_change = (rejudge or (stale_map and old_map is not None)) \
            and was_priority.get(inc["id"]) == "low" and inc["priority"] == "high"
        if look_budget > 0 and look_back(inc):
            look_budget -= 1
        if inc["priority"] != "high" or inc.get("status") == "held":
            if inc.get("status") == "pending" and inc.get("alerted_verdict") is None and sea_finder_doubt(inc):
                inc["status"] = "new"      # it waited for the closer look, which did not show it
            continue
        if inc.get("early_promotion_ambiguous"):
            continue  # several original spots cannot be assigned to one Telegram thread safely
        if day_requires_confirmation(inc) or association_requires_confirmation(inc) or inc.get("recovered_historical"):
            continue  # experimental day evidence cannot send alerts or updates without corroboration
        rank = VERDICT_RANK[inc["verdict"]]
        if inc.get("alerted_verdict") is None and rank >= min_rank and not SW.get("alerts") \
                and set(inc.get("srcs") or []) <= seawatch.FINDER_SOURCES:
            continue  # the sea finder is still on trial: shown in the app, not sent, until others confirm
        if inc.get("alerted_verdict") is None and rank >= min_rank:
            if too_old_for_first_alert(inc, now, max_age, by_change):
                # e.g. the first run after installing: shown in the app, not pushed as news
                inc["alerted_verdict"], inc["status"] = inc["verdict"], "late"
                log.info("not alerting %s: too old to be news (%s)", inc["id"], max_age)
                continue
            if waits_for_closer_look(inc):
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

    # heat patterns (silent): new sustained or wide heat that started at another such place within
    # hours is tagged in the app; its reasons line is written again
    try:
        for inc in patterns.link_sites(S.incidents, cfg, now):
            patterns.apply(inc, cfg)
            patterns.refresh_signals(inc, cfg)
    except Exception as e:  # noqa: BLE001 - a silent label must never stop a check
        log.warning("heat patterns: linking places failed: %s", e)

    def post_early(message, media=None, reply_to=None):
        result = post(message, media, reply_to=reply_to)
        # Printing a dry run must not consume the first real notification.
        return result if send and telegram_ready(cfg) else None

    earlyalerts.deliver(cfg, S, now, post_early, prepare_clip)

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
            message += "\n" + video_note(inc)
        if cfg["satpic"].get("enabled", True) and (inc.get("place") != "sea" or cfg["satpic"].get("sea")):
            message += "\n🛰 صورة «قبل وبعد» للموقع تُرسل هنا إذا التقط القمر الأوروبي \u2068Sentinel-2\u2069\u200f صورة صافية له في الأيام القادمة."
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
        early_confirmation = bool(inc.get("early_ids")) and inc.get("early_video_sent_at") != iso(now)
        with_video = intensity or early_confirmation
        if with_video and prepare_clip(inc, refresh=True) and inc.get("clip"):
            path = Path(cfg["_site_dir"]) / inc["clip"]
            if path.exists():
                media = path
        elif early_confirmation and not intensity and inc.get("early_clip") and inc.get("clip"):
            # The source-grid evidence remains useful for a confirmation-only
            # reply, with its original time printed in the video. It must never
            # stand in for a video covering a later measured intensity peak.
            path = Path(cfg["_site_dir"]) / inc["clip"]
            if path.exists():
                media = path
        message = alert_message(inc, cfg, update=True, threaded=bool(first_post),
                                intensity=intensity, delayed=_delayed_update(inc, cfg, now))
        if with_video and media is None and cfg["clip"].get("enabled", True):
            message += "\n🎞 الفيديو المحدث قيد التجهيز؛ سيُرسل عند توفر صور تُظهر الرصد الجديد بوضوح."
        if post(message, media, reply_to=first_post):
            S.sent = True
            inc["alerted_verdict"] = inc["verdict"]
            _remember_post(inc, now)
            if with_video:
                inc["clip_video_pending"] = bool(cfg["clip"].get("enabled", True) and media is None)
            log.info("UPDATE %s -> %s%s", inc["id"], inc["verdict"], " (stronger)" if intensity else "")

    # If the newest pictures were late, the strength update went out immediately as text.
    # Retry its media separately without repeating the same rise notification every run.
    for inc in S.incidents:
        if inc["id"] in updated_ids or not inc.get("clip_video_pending") \
                or inc.get("status") != "alerted" or inc.get("priority") != "high" \
                or inc.get("early_promotion_ambiguous") \
                or day_requires_confirmation(inc) or association_requires_confirmation(inc) or inc.get("recovered_historical") \
                or now - parse_time(inc["last"]) > update_max_age:
            continue
        if inc.get("early_video_sent_at") == iso(now) and peak_reading(inc) is None:
            inc["clip_video_pending"] = False
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

    # The before/after satellite picture: asked for after the alert, posted once as a reply when
    # the worker has it (a clear pass comes within days). No network here.
    cfg["satpic"]["_since"] = S.state.setdefault("satpic_since", iso(now - dt.timedelta(days=3)))
    pictures_left = 1                    # one per check: several ready at once go out minutes apart
    for inc in S.incidents:
        if not satpic.wanted(inc, cfg, now):
            continue
        satpic.request(cfg, inc, now)
        ready = satpic.collect(cfg, inc, now) if pictures_left else None
        if not ready:
            continue
        picture, result = ready
        pictures_left -= 1
        if post(satpic.caption(inc, result, cfg), picture, reply_to=inc.get("tg_msg")):
            S.sent = True
            inc["satpic"] = dict(satpic.facts(inc, result), sent_at=iso(now))
            try:
                patterns.refresh_signals(inc, cfg)
            except Exception as e:  # noqa: BLE001 - a silent signal must never stop a check
                log.warning("signals for %s: %s", inc["id"], e)
            log.info("SATELLITE PICTURE %s: %s -> %s", inc["id"], inc["satpic"]["before"], inc["satpic"]["after"])

    # Watched places: a new clear pass with a patch of clear change is posted once, as its own
    # post; a pass without one, and the first pass (the starting point), are only remembered.
    for place, result, picture in satpic.watch_results(cfg, S.state, now):
        seen = S.state.setdefault("watch", {})
        if picture is None:
            seen[place["key"]] = {"pass": result["pass"], "name": place["name"]}
        elif pictures_left:
            pictures_left -= 1
            if post(satpic.watch_caption(place, result, cfg), picture):
                S.sent = True
                seen[place["key"]] = {"pass": result["pass"], "name": place["name"], "sent_at": iso(now),
                                      "patch_ha": result.get("patch_ha")}
                log.info("WATCHED PLACE %s: change posted (%s)", place["name"], result["pass"])

    # Smoke, a second witness by day: a daytime fire is given to the worker, and what it finds is
    # written on the incident. Seen from a fire that was posted, it goes out once as a reply under
    # that post (the owner made it official on 22 Nov 2026: on its first 117 fires it showed on 3,
    # all real heat, two of them confirmed fires). It never changes the verdict.
    if cfg["smoke"].get("enabled", True):
        for inc in S.incidents:
            asked = smoke.wanted(inc, cfg, now)
            if asked:
                smoke.request(cfg, inc, now)
            if asked or (inc.get("smoke") and not inc["smoke"].get("closed") and not inc["smoke"].get("seen")):
                smoke.collect(cfg, inc, now)
            if smoke.due_post(inc, cfg, now):
                if post(smoke.message(inc, cfg), reply_to=inc.get("tg_msg")):
                    S.sent = True
                    inc["smoke_posted"] = iso(now)
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
    # an alerted fire still waiting for its before/after satellite picture is kept until it comes
    S.incidents = [i for i in S.incidents if now - parse_time(i["last"]) <= keep or satpic.wanted(i, cfg, now)]
    mem.prune(today, int(M["window_days"]) + 5)
    flares.seed_reviewed(S.state, now, FL)
    for token in flares.tidy(S.state, now, FL):
        mediaqueue.discard(cfg, token)
    if cfg.get("_background_media"):
        flares.ask_videos(S.state, cfg, now, mediaqueue.request_clip)
    S.state["slots"] = {k: v for k, v in slots.items()
                        if now - parse_time(k.rsplit("|", 1)[1]) <= dt.timedelta(hours=6)}
    S.state["seen"] = {k: v for k, v in S.state.get("seen", {}).items()
                       if now - parse_time(v) <= dt.timedelta(days=3)}
    # a NOAA pixel waits for its FIRMS copy as long as FIRMS rows can still come (two days asked);
    # once paired, its copy is in `seen` and the pair is not needed any more
    S.state["noaa_seen"] = {k: v for k, v in S.state.get("noaa_seen", {}).items()
                            if not v and now - parse_time(k.split("|")[1]) <= dt.timedelta(days=2)}
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
    if cfg.get("_background_media"):
        try:   # the watching loop checks again as soon as one of these videos is made
            mediaqueue.write_awaited(cfg, awaited_videos(S))
        except Exception as e:  # noqa: BLE001
            log.warning("videos awaited by posts not written: %s", e)
    publish(cfg, S, status, now, learning, len(dets), finished=finished, duration=elapsed)
    try:   # the long-term record the app searches; it must never stop a check
        archive.update(cfg, S.incidents, finished)
    except Exception as e:  # noqa: BLE001
        log.warning("archive not updated: %s", e)
    return S


PUBLIC = ["id", "kind", "headline", "where", "country", "place", "facility", "start_text", "last_text",
          "first", "last", "lat", "lon", "best", "size", "max_frp", "verdict", "reason", "priority",
          "sources_text", "regions", "region_name", "clip", "snap", "clip_note", "frp_series",
          "clip_video_pending", "clip_needs_refresh", "clip_updated_at", "clip_max_frp",
          "recovered_historical", "association_uncertain", "association", "correction_note", "satpic", "smoke",
          "early_ids", "early_promotion_ambiguous",
          # the assessment (assess.py): what kind of event, how sure and why, how serious
          "category", "category_text", "confidence", "confidence_level", "confidence_label", "severity",
          "known_days", "typical_frp", "site_record", "site_habit", "new_heat",
          # the heat pattern (patterns.py): a label with its reasons, shown in the app only
          "pattern",
          # signals worth attention (patterns.signals_of), app only
          "signals"]
# the longer parts of the assessment go out only for events worth opening, to keep the feed light
PUBLIC_DETAIL = ["evidence", "sources", "agreement"]


def public_item(inc):
    """What the app receives for one incident."""
    item = {k: inc.get(k) for k in PUBLIC}
    if inc.get("priority") == "high" or inc.get("verdict") != "يحتاج تأكيداً":
        for k in PUBLIC_DETAIL:
            item[k] = inc.get(k)
        item["timeline"] = assess.timeline_of(inc)
    item["alerted"] = bool(inc.get("alerted_at"))   # went out to the channel
    return item


def publish(cfg, S, status, now, learning, n_dets, *, finished=None, duration=None):
    site = Path(cfg["_site_dir"])
    finished = finished or utcnow()
    partial = any(row.get("pending_recent", 0) or row.get("failed", 0) or row.get("deferred", 0)
                  or (not row.get("ok", False) and key != "recovery"
                      and not row.get("experimental"))
                  for key, row in status.items() if key != "recovery")
    items = [public_item(i) for i in S.incidents if i.get("headline")]
    early = earlyalerts.items(cfg, S.state, S.incidents, finished)
    for item in early:   # first night specks: shown with what they are and how little is known yet
        assess.assess(item, cfg)
        item.setdefault("alerted", False)
    items.extend(early)
    for item in items:
        if (item.get("clip_video_pending") or item.get("clip_needs_refresh")) \
                and (item.get("clip") or not item.get("clip_note")):
            item["clip_status_text"] = "يجري تجهيز فيديو محدث وفق القياسات الجديدة"
        elif item.get("clip"):
            item["clip_status_text"] = ""
        elif not cfg["clip"].get("enabled", True):
            item["clip_status_text"] = "إنشاء الفيديو غير مفعّل حالياً"
        elif item.get("clip_note"):
            item["clip_status_text"] = item["clip_note"]
        elif item.get("priority") == "high" and now - parse_time(item["last"]) <= dt.timedelta(days=2):
            item["clip_status_text"] = "يظهر الفيديو هنا إن ظهر الحدث بوضوح في الصور"
        else:
            item["clip_status_text"] = "لا يتوفر فيديو لهذا الحدث حالياً"
        item["smoke_text"] = smoke.text(item.get("smoke"))
    items.sort(key=lambda i: i["last"], reverse=True)
    # every important incident, but only the latest small ones, so the app stays light on a phone
    low = [i for i in items if i["priority"] != "high"][250:]
    if low:
        drop = {id(i) for i in low}
        items = [i for i in items if id(i) not in drop]
    save_json(site / "data" / "incidents.json", {"generated": iso(finished), "items": items}, compact=True)
    # silent, for the app only: plants whose flare burns far busier than usual (flares.py)
    busy = flares.items(S.state, now, {**flares.DEFAULTS, **cfg.get("flares", {})})
    save_json(site / "data" / "meta.json", {
        "generated": iso(finished), "scan_started": iso(now), "scan_finished": iso(finished),
        "scan_duration_s": round(duration if duration is not None else max(0, (finished - now).total_seconds()), 1),
        "scan_partial": bool(partial), "app_name": cfg["app_name"], "learning": learning,
        "detections_this_run": n_dets, "local_offset_hours": cfg["local_time"]["offset_hours"],
        "local_name": cfg["local_time"]["name"],
        "regions": [{"id": r["id"], "name": r["name"], "bbox": r["bbox"]} for r in cfg["regions"]],
        # the seas the night finder reads at full detail (for the app's coverage layer)
        "sea_watch": [{"id": b["id"], "bbox": b["bbox"]} for b in cfg["seawatch"].get("boxes", [])
                      if cfg["seawatch"].get("enabled") and b.get("enabled", True)],
        "sources": status, "telegram": bool(cfg["telegram"].get("bot_token")),
        "flare_activity": busy})
    # remove clips of incidents that are gone
    keep = {i.get(k) for i in items + busy for k in ("clip", "snap")}
    clips = site / "clips"
    if clips.exists():
        for f in clips.iterdir():
            if f"clips/{f.name}" not in keep:
                f.unlink()
