"""One monitoring cycle: collect detections, judge them, alert, and publish the app feed."""
from __future__ import annotations

import datetime as dt
import math
import os
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

import yaml

from .brain import (VERDICT_RANK, Memory, add_to_incident, alert_message, finalize, find_incident,
                    nearby_confirmed, new_incident)
from .clip import NotVisible, make_clip
from .geodb import Geo
from . import seawatch
from .notify import deliver, telegram_ready, tg_send
from .sources import detect_geo_layer, fetch_frp, firms_fetch, region_tiles, union_bbox
from .updates import intensity_update_due, peak_reading
from .util import (floor_time, in_bbox, iso, load_json, log, make_session, parse_time, save_json,
                   utcnow)
from .wms import WMS, Availability, latest_time

STALE_HOURS = 3
RULES = 11  # bump when the importance rules change, so stored incidents are judged again
CLIP_STYLE = 16  # bump when clips change look, so the app's clips are made again
SEA_CHECK = 1    # bump when the closer look changes: clips of the sea finder's own finds are checked again

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
               "intensity_ratio": 1.5, "intensity_add_mw": 25, "intensity_interval_min": 30,
               "focus_countries": ["العراق", "السعودية", "الإمارات", "قطر", "البحرين", "الكويت", "عمان",
                                   "عُمان", "إيران", "اليمن"]},
    "memory": {"persistent_min_days": 2, "window_days": 30, "unusual_ratio": 5, "unusual_add_mw": 100,
               "unusual_facility_km": 3},
    "incidents": {"merge_km": 6, "merge_hours": 12, "keep_days": 7},
    "firms": {"map_key": "", "base_url": "https://firms.modaps.eosdis.nasa.gov",
              "sources": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"],
              "bootstrap_days": 10, "max_day_range": 5, "live_hours": 36},
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
    # Rasad's own finder of new warm specks at sea at night, on Meteosat-12 pictures (see seawatch.py)
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
        inc["place"] = geo.place_kind(inc["lat"], inc["lon"])
        inc["facility"] = geo.facility(inc["lat"], inc["lon"])
        inc["where"], inc["country"] = geo.describe(inc["lat"], inc["lon"], inc["place"])
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
    # the heat panel uses the red part of the Fire Temperature RGB only: that is the 3.8 micron
    # temperature (0..60 C, gamma 0.4); green and blue are daylight reflections that make clouds
    # look like heat
    mtg = dict(E["clip_layer"], name=W["layer"], cadence_min=W["cadence_min"], label=W["label"],
               hot_is_bright=True, channel=0, bt=[273.15, 333.15, 0.4])
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
        inc["clip_max_frp"] = inc.get("max_frp")
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
        if inc.get("srcs") and set(inc["srcs"]) <= {seawatch.SRC}:
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


def run_cycle(cfg, send=True, alert_now=False):
    now = utcnow()
    today = now.date().isoformat()
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    S = State(cfg)
    geo = Geo(cfg, session, wms, S.state)
    geo.self_test()
    E, M, I = cfg["eumetview"], cfg["memory"], cfg["incidents"]
    regions = cfg["regions"]
    box = union_bbox([r["bbox"] for r in regions])
    tiles = region_tiles(regions, float(E["tile_deg"]))
    pcenter = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    pbox = [pcenter[0] - 0.2, pcenter[1] - 0.2, pcenter[0] + 0.2, pcenter[1] + 0.2]
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

    # 1) European weather satellites, every 10-15 minutes
    dets, n_images, t0 = [], 0, time.time()
    slots = S.state.setdefault("slots", {})
    avail = Availability(wms, S.state, now)
    late_budget = float(E.get("catchup_budget_s", 75))   # seconds per run for slots missed earlier
    for L in E["fire_layers"]:
        if not L.get("enabled", True):
            continue
        cad = int(L["cadence_min"])
        t_latest = latest_time(wms, S.state, L.get("clock_layer") or L["name"], cad, pbox)
        fresh = t_latest is not None and now - t_latest <= dt.timedelta(hours=STALE_HOURS)
        status[L["id"]] = {"name": L.get("label_ar", L["id"]), "ok": fresh,
                           "latest": iso(t_latest) if t_latest else None}
        if not fresh:
            log.warning("%s: no recent data", L["name"])
            continue
        # the newest slots every run, then (within a small budget) any slot of the last hours that
        # was never read: after an outage at the source the pictures can come out late
        n_new = int(E["lookback_slots"])
        span = max(n_new, int(round(float(E.get("catchup_hours", 2)) * 60 / cad)))
        late_done = 0
        for k in range(span):
            t = t_latest - dt.timedelta(minutes=cad * k)
            age = (now - t).total_seconds() / 60
            late = k >= n_new
            failed = 0
            todo = []
            for tile in tiles:
                key = f"{L['id']}|{','.join(map(str, tile))}|{iso(t)}"
                prev = slots.get(key)
                if prev is not None and (late or prev >= E["settle_minutes"] or age < E["settle_minutes"]):
                    continue
                todo.append((key, tile))
            if not todo:
                continue
            if late and (late_done >= 2 or time.time() - t0 > late_budget):
                continue
            if k and not avail.has(L["name"], t):
                continue   # a hole at the source: the server would only send another time's picture
            if late:
                late_done += 1
                log.info("%s %s: reading a slot missed earlier", L["id"], iso(t))
            # the tiles are asked for in parallel: the server is slow per picture, not per request
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda kt: detect_geo_layer(
                    wms, dict(L, tile_px=L.get("tile_px", E["tile_px"])), kt[1], t), todo))
            for (key, tile), res in zip(todo, results):
                n_images += 1
                if res is None:
                    failed += 1  # not ready yet or server busy: retried next run
                else:
                    dets += res
                    slots[key] = age
            if failed:
                log.info("%s %s: %d of %d images not available yet, will retry", L["id"], iso(t), failed, len(tiles))

    log.info("European satellites: %d images read, %d fire points, %.0f s", n_images, len(dets), time.time() - t0)

    # 1b) Rasad's own finder: new warm specks at sea at night on Meteosat-12 (small ship fires)
    SW = cfg["seawatch"]
    if SW.get("enabled"):
        t1 = time.time()
        try:
            spots = seawatch.watch(cfg, wms, S.state, now, Memory(S.memory), geo, avail)
            ok = True
        except Exception as e:  # noqa: BLE001
            log.warning("sea watch failed: %s", e)
            spots, ok = [], False
        dets += spots
        status["seawatch"] = {"name": "كاشف رصد للبحر ليلاً (Meteosat-12)", "ok": ok,
                              "latest": max((d["t"] for d in spots), default=None)}
        log.info("sea watch: %d spot(s), %.0f s", len(spots), time.time() - t1)

    # 2) NASA (VIIRS/MODIS), precise but a few hours late.
    #    Ask for today AND yesterday (UTC): the night passes over the Gulf (about 21:30-23:30 UTC)
    #    are published after midnight UTC, so "today only" would never see them.
    if cfg["firms"]["map_key"]:
        seen = S.state.setdefault("seen", {})
        polar = firms_fetch(session, cfg, box, 2)
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
        status["firms"] = {"name": "الأقمار الأمريكية (ناسا)", "ok": True, "latest": max(
            (d["t"] for d in polar), default=None)}

    # drop duplicates of the same symbol found in two neighbouring tiles
    uniq = {}
    for d in dets:
        uniq.setdefault((d["src"], d["t"], round(d["lat"], 2), round(d["lon"], 2)), d)
    dets = sorted(uniq.values(), key=lambda d: d["t"])

    # 3) judgement
    mem = Memory(S.memory)
    avail = mem.days_known(today, int(M["window_days"]))  # days of real history, not days the bot ran
    learning = avail == 0 and not alert_now
    merge_km, merge_h = float(I["merge_km"]), float(I["merge_hours"])
    open_incs = [i for i in S.incidents if now - parse_time(i["last"]) <= dt.timedelta(hours=merge_h)]
    touched, frp_budget = set(), 40
    for d in dets:
        if not any(in_bbox(r["bbox"], d["lat"], d["lon"]) for r in regions):
            continue
        day = d["t"][:10]
        if d.get("_history"):
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
                and frp_budget > 0 and d.get("_fi"):
            steady_facility = geo.facility_within(d["lat"], d["lon"], float(M["unusual_facility_km"]))
            if steady_facility:
                frp_budget -= 1
                fetch_frp(wms, d)
                frp = d.get("frp")
        unusual = steady and frp is not None and typical is not None and \
            frp >= max(float(M["unusual_ratio"]) * typical, typical + float(M["unusual_add_mw"])) and \
            (steady_facility if steady_facility is not None else
             geo.facility_within(d["lat"], d["lon"], float(M["unusual_facility_km"])))
        routine = known and not unusual
        mem.record(d["lat"], d["lon"], day, frp)
        inc = find_incident(open_incs, d, 2.0 if routine else merge_km, merge_h)
        if inc is None and (routine or learning):
            continue
        if inc is not None and routine and inc["kind"] == "unusual":
            continue  # the usual flare is not evidence of the unusual heat
        if d["kind"] == "geo" and frp_budget > 0 and d.get("_fi"):
            frp_budget -= 1
            fetch_frp(wms, d)
        d.pop("_fi", None)
        if inc is None:
            inc = new_incident(d, "unusual" if unusual else "new")
            inc["known_days"], inc["typical_frp"] = n_days, typical
            # days with heat within ~10 km: farmland burnt every season and flare fields are busy
            inc["area_days"] = mem.profile(d["lat"], d["lon"], day, int(M["window_days"]), 5)[0]
            S.incidents.append(inc)
            open_incs.append(inc)
        add_to_incident(inc, d)
        touched.add(inc["id"])
    log.info("judged %d detections: %d incidents touched, %d heat cells remembered",
             len(dets), len(touched), len(mem.cells))
    if learning:
        log.info("learning period: recording the usual heat only (no alerts until tomorrow)")

    # 4) describe, decide, alert
    min_rank = VERDICT_RANK.get(cfg["alerts"]["min_verdict"], 1)
    max_age = dt.timedelta(hours=float(cfg["alerts"].get("max_age_hours", 8)))
    first_alerts, updates = [], []
    look_budget = 2

    def look_back(inc):
        """NASA saw a fire at sea hours late: find when it first showed on Meteosat-12."""
        fac = inc.get("facility")
        if not (SW.get("look_back") and inc.get("place") in ("sea", "coast") and inc.get("polar")
                and seawatch.SRC not in (inc.get("srcs") or []) and not inc.get("looked_back")
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
            if inc.get("srcs") and set(inc["srcs"]) <= {seawatch.SRC} and len(inc.get("families") or []) < 2 \
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
            if inc.get("srcs") and set(inc["srcs"]) <= {seawatch.SRC} and inc.get("clip_note") and not inc.get("clip"):
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
        rank = VERDICT_RANK[inc["verdict"]]
        if inc.get("alerted_verdict") is None and rank >= min_rank and not SW.get("alerts") \
                and set(inc.get("srcs") or []) <= {seawatch.SRC}:
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
            if inc.get("status") == "alerted" and now - parse_time(inc["last"]) <= max_age:
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
        deliver(cfg, session, summary, None, send)
        first_alerts = []

    for inc in first_alerts:
        inc["status"] = "pending"
        media = None
        ready = add_clip(cfg, wms, S, inc, now)
        if ready and inc.get("clip"):
            media = Path(cfg["_site_dir"]) / inc["clip"]
        sent = deliver(cfg, session, alert_message(inc, cfg), media, send)
        if sent:
            S.sent = True
            inc["alerted_verdict"], inc["status"], inc["alerted_at"] = inc["verdict"], "alerted", iso(now)
            _remember_post(inc, now)
            if sent is not True:
                inc["tg_msg"] = sent  # updates answer this post
            log.info("ALERT %s: %s (%s)", inc["id"], inc["headline"], inc["verdict"])
    updated_ids = set()
    for inc, intensity in updates:
        updated_ids.add(inc["id"])
        first_post = inc.get("tg_msg")
        media = None
        if intensity and add_clip(cfg, wms, S, inc, now, refresh=True) and inc.get("clip"):
            path = Path(cfg["_site_dir"]) / inc["clip"]
            if path.exists():
                media = path
        if deliver(cfg, session, alert_message(inc, cfg, update=True, threaded=bool(first_post),
                                               intensity=intensity), media, send,
                   reply_to=first_post):
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
                or now - parse_time(inc["last"]) > max_age:
            continue
        if not add_clip(cfg, wms, S, inc, now, refresh=True) or not inc.get("clip"):
            continue
        media = Path(cfg["_site_dir"]) / inc["clip"]
        if not media.exists():
            continue
        first_post = inc.get("tg_msg")
        if deliver(cfg, session, alert_message(inc, cfg, update=True, threaded=bool(first_post),
                                               video_update=True), media, send, reply_to=first_post):
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
        if budget <= 0:
            break
        needs_refresh = bool(inc.get("clip_needs_refresh"))
        if inc.get("priority") == "high" and (not inc.get("clip") or needs_refresh) and \
                now - parse_time(inc["last"]) <= dt.timedelta(days=2):
            tried = inc.get("clip_refresh_tried" if needs_refresh else "clip_tried")
            retry = float(cfg["clip"].get("refresh_retry_min", 10)) if needs_refresh else 60
            if not tried or now - parse_time(tried) >= dt.timedelta(minutes=retry):
                add_clip(cfg, wms, S, inc, now, refresh=needs_refresh)
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
    S.state["last_run"] = iso(now)
    S.save()
    publish(cfg, S, status, now, learning, len(dets))
    return S


PUBLIC = ["id", "kind", "headline", "where", "country", "place", "facility", "start_text", "last_text",
          "first", "last", "lat", "lon", "best", "size", "max_frp", "verdict", "reason", "priority",
          "sources_text", "regions", "region_name", "clip", "snap", "clip_note", "frp_series",
          "clip_video_pending", "clip_needs_refresh", "clip_updated_at", "clip_max_frp"]


def publish(cfg, S, status, now, learning, n_dets):
    site = Path(cfg["_site_dir"])
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
    save_json(site / "data" / "incidents.json", {"generated": iso(now), "items": items}, compact=True)
    save_json(site / "data" / "meta.json", {
        "generated": iso(now), "app_name": cfg["app_name"], "learning": learning,
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
