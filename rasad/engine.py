"""One monitoring cycle: collect detections, judge them, alert, and publish the app feed."""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from pathlib import Path

import yaml

from .brain import (VERDICT_RANK, Memory, add_to_incident, alert_message, finalize, find_incident,
                    new_incident)
from .clip import make_clip
from .geodb import Geo
from .notify import deliver
from .sources import detect_geo_layer, fetch_frp, firms_fetch, region_tiles, union_bbox
from .util import (floor_time, in_bbox, iso, load_json, log, make_session, parse_time, save_json,
                   utcnow)
from .wms import WMS, latest_time

STALE_HOURS = 3

DEFAULTS = {
    "app_name": "رصد",
    "app_url": "",
    "watermark": "",
    "local_time": {"offset_hours": 3, "name": "بغداد"},
    "paths": {"state": "data/state", "site": "data/site", "geodata": "data/state/geo"},
    "regions": [],
    "alerts": {"min_verdict": "مرجّح", "near_facility_km": 10, "big_fire_mw": 50},
    "memory": {"persistent_min_days": 2, "window_days": 30, "unusual_ratio": 3, "unusual_add_mw": 40},
    "incidents": {"merge_km": 6, "merge_hours": 12, "keep_days": 7},
    "firms": {"map_key": "", "base_url": "https://firms.modaps.eosdis.nasa.gov",
              "sources": ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"],
              "bootstrap_days": 10},
    "telegram": {"bot_token": "", "chat_id": "", "api_base": "https://api.telegram.org"},
    "eumetview": {
        "wms_url": "https://view.eumetsat.int/geoserver/wms",
        "tile_deg": 8,
        "tile_px": 1000,
        "lookback_slots": 2,
        "settle_minutes": 25,
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
    "clip": {"enabled": True, "frames": 8, "frames_after": 2, "half_size_km": 40, "native_px": 320,
             "out_px": 1280, "fps": 3, "hold_last": 4, "format": "mp4"},
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
        self.state = load_json(self.dir / "state.json", {})
        self.memory = load_json(self.dir / "memory.json", {})
        self.incidents = load_json(self.dir / "incidents.json", [])

    def save(self):
        save_json(self.dir / "state.json", self.state, compact=True)
        save_json(self.dir / "memory.json", self.memory, compact=True)
        save_json(self.dir / "incidents.json", self.incidents, compact=True)


def available_days(state, today, window):
    lo = (dt.date.fromisoformat(today) - dt.timedelta(days=window)).isoformat()
    return sum(1 for d in state.get("run_days", []) if lo <= d < today)


def bootstrap(cfg, session, S, box):
    """Learn the usual heat (gas flares, plants) from the last days of NASA data."""
    days = int(cfg["firms"].get("bootstrap_days", 10))
    start = (utcnow().date() - dt.timedelta(days=days)).isoformat()
    mem = Memory(S.memory)
    dets = firms_fetch(session, cfg, box, min(days, 10), date=start)
    today = utcnow().date().isoformat()
    for d in dets:
        if d["t"][:10] < today:
            mem.record(d["lat"], d["lon"], d["t"][:10], d.get("frp"))
    for k in range(1, days + 1):
        day = (utcnow().date() - dt.timedelta(days=k)).isoformat()
        if day not in S.state.setdefault("run_days", []):
            S.state["run_days"].append(day)
    log.info("learned usual heat from %d NASA detections over %d days", len(dets), days)
    return len(dets)


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
        region, ids = smallest_region(cfg, inc["lat"], inc["lon"])
        inc["regions"] = ids
        inc["region_name"] = region["name"] if region else None
        inc["_pos"] = pos
    return finalize(inc, cfg)


def clip_for(cfg, wms, S, inc):
    L = cfg["eumetview"]["clip_layer"]
    cad = int(L["cadence_min"])
    pbox = [inc["lon"] - 0.2, inc["lat"] - 0.2, inc["lon"] + 0.2, inc["lat"] + 0.2]
    latest = latest_time(wms, S.state, L["name"], cad, pbox)
    if latest is None or utcnow() - latest > dt.timedelta(hours=STALE_HOURS):
        raise RuntimeError("no recent thermal images")
    after = int(cfg["clip"].get("frames_after", 2))
    t_end = min(latest, floor_time(parse_time(inc["first"]), cad) + dt.timedelta(minutes=cad * after))
    base = Path(cfg["_site_dir"]) / "clips" / inc["id"]
    video, snap = make_clip(cfg, wms, inc["lat"], inc["lon"], t_end, base)
    return video, snap


def run_cycle(cfg, send=True, alert_now=False):
    now = utcnow()
    today = now.date().isoformat()
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    S = State(cfg)
    geo = Geo(cfg, session, wms, S.state)
    E, M, I = cfg["eumetview"], cfg["memory"], cfg["incidents"]
    regions = cfg["regions"]
    box = union_bbox([r["bbox"] for r in regions])
    tiles = region_tiles(regions, float(E["tile_deg"]))
    pcenter = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    pbox = [pcenter[0] - 0.2, pcenter[1] - 0.2, pcenter[0] + 0.2, pcenter[1] + 0.2]
    status = {}

    if cfg["firms"]["map_key"] and not S.state.get("bootstrapped"):
        bootstrap(cfg, session, S, box)
        S.state["bootstrapped"] = iso(now)

    # 1) European weather satellites, every 10-15 minutes
    dets, n_images, t0 = [], 0, time.time()
    slots = S.state.setdefault("slots", {})
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
        for k in range(int(E["lookback_slots"])):
            t = t_latest - dt.timedelta(minutes=cad * k)
            age = (now - t).total_seconds() / 60
            failed = 0
            for tile in tiles:
                key = f"{L['id']}|{','.join(map(str, tile))}|{iso(t)}"
                prev = slots.get(key)
                if prev is not None and (prev >= E["settle_minutes"] or age < E["settle_minutes"]):
                    continue
                res = detect_geo_layer(wms, dict(L, tile_px=L.get("tile_px", E["tile_px"])), tile, t)
                n_images += 1
                if res is None:
                    failed += 1  # not ready yet or server busy: retried next run
                else:
                    dets += res
                    slots[key] = age
            if failed:
                log.info("%s %s: %d of %d images not available yet, will retry", L["id"], iso(t), failed, len(tiles))

    log.info("European satellites: %d images read, %d fire points, %.0f s", n_images, len(dets), time.time() - t0)

    # 2) NASA (VIIRS/MODIS), precise but a few hours late
    if cfg["firms"]["map_key"]:
        seen = S.state.setdefault("seen", {})
        polar = firms_fetch(session, cfg, box, 1)
        fresh_polar = []
        for d in polar:
            k = f"{d['src']}|{d['t']}|{d['lat']}|{d['lon']}"
            if k not in seen:
                seen[k] = d["t"]
                fresh_polar.append(d)
        dets += fresh_polar
        status["firms"] = {"name": "الأقمار الأمريكية (ناسا)", "ok": True, "latest": max(
            (d["t"] for d in polar), default=None)}

    # drop duplicates of the same symbol found in two neighbouring tiles
    uniq = {}
    for d in dets:
        uniq.setdefault((d["src"], d["t"], round(d["lat"], 2), round(d["lon"], 2)), d)
    dets = sorted(uniq.values(), key=lambda d: d["t"])

    # 3) judgement
    mem = Memory(S.memory)
    avail = available_days(S.state, today, int(M["window_days"]))
    learning = avail == 0 and not alert_now
    merge_km, merge_h = float(I["merge_km"]), float(I["merge_hours"])
    open_incs = [i for i in S.incidents if now - parse_time(i["last"]) <= dt.timedelta(hours=merge_h)]
    touched, frp_budget = set(), 40
    for d in dets:
        if not any(in_bbox(r["bbox"], d["lat"], d["lon"]) for r in regions):
            continue
        day = d["t"][:10]
        n_days, typical = mem.profile(d["lat"], d["lon"], day, int(M["window_days"]))
        known = avail > 0 and n_days >= min(int(M["persistent_min_days"]), avail)
        frp = d.get("frp")  # NASA gives the strength directly; European symbols are asked later
        unusual = known and frp is not None and typical is not None and \
            frp >= max(float(M["unusual_ratio"]) * typical, typical + float(M["unusual_add_mw"]))
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
    for inc in S.incidents:
        recent = now - parse_time(inc["last"]) <= dt.timedelta(hours=24)
        stale_map = recent and (inc.get("_pos") or [None])[-1] != geo.version
        if inc["id"] not in touched and not (recent and inc.get("status") == "pending") and not stale_map:
            continue
        enrich(inc, cfg, geo)
        if inc["priority"] != "high":
            continue
        rank = VERDICT_RANK[inc["verdict"]]
        if inc.get("alerted_verdict") is None and rank >= min_rank:
            inc["status"] = "pending"
            media = None
            if cfg["clip"].get("enabled", True) and not inc.get("clip"):
                try:
                    video, snap = clip_for(cfg, wms, S, inc)
                    site = Path(cfg["_site_dir"])
                    inc["clip"] = video.relative_to(site).as_posix()
                    inc["snap"] = snap.relative_to(site).as_posix()
                except Exception as e:  # noqa: BLE001
                    log.warning("clip for %s failed: %s", inc["id"], e)
            if inc.get("clip"):
                media = Path(cfg["_site_dir"]) / inc["clip"]
            if deliver(cfg, session, alert_message(inc, cfg), media, send):
                inc["alerted_verdict"], inc["status"], inc["alerted_at"] = inc["verdict"], "alerted", iso(now)
                log.info("ALERT %s: %s (%s)", inc["id"], inc["headline"], inc["verdict"])
        elif inc.get("alerted_verdict") and rank > VERDICT_RANK[inc["alerted_verdict"]]:
            if deliver(cfg, session, alert_message(inc, cfg, update=True), None, send):
                inc["alerted_verdict"] = inc["verdict"]
                log.info("UPDATE %s -> %s", inc["id"], inc["verdict"])

    # 5) tidy up and publish
    keep = dt.timedelta(days=int(I["keep_days"]))
    S.incidents = [i for i in S.incidents if now - parse_time(i["last"]) <= keep]
    mem.prune(today, int(M["window_days"]) + 5)
    S.state["slots"] = {k: v for k, v in slots.items()
                        if now - parse_time(k.rsplit("|", 1)[1]) <= dt.timedelta(hours=6)}
    S.state["seen"] = {k: v for k, v in S.state.get("seen", {}).items()
                       if now - parse_time(v) <= dt.timedelta(days=2)}
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
          "sources_text", "regions", "clip", "snap"]


def publish(cfg, S, status, now, learning, n_dets):
    site = Path(cfg["_site_dir"])
    items = [{k: i.get(k) for k in PUBLIC} for i in S.incidents if i.get("headline")]
    items.sort(key=lambda i: i["last"], reverse=True)
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
