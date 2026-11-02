"""Command line: python -m rasad <command>"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path

from .engine import STALE_HOURS, State, load_config, run_cycle
from .geodb import build_geodata, geodata_status
from .notify import telegram_ready, tg_send
from .sources import firms_fetch, region_tiles, union_bbox
from .util import VERSION, floor_time, iso, log, make_session, parse_time, utcnow
from .wms import WMS, latest_time


def geo_needed(cfg):
    age, in_progress = geodata_status(cfg)
    return in_progress or age is None or age > float(cfg["geodata"]["rebuild_days"])


def cmd_status(cfg):
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    S = State(cfg)
    box = union_bbox([r["bbox"] for r in cfg["regions"]])
    c = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    pbox = [c[0] - 0.2, c[1] - 0.2, c[0] + 0.2, c[1] + 0.2]
    print(f"رصد {VERSION}")
    print("المناطق: " + "، ".join(r["name"] for r in cfg["regions"]))
    print(f"عدد مربعات المراقبة: {len(region_tiles(cfg['regions'], float(cfg['eumetview']['tile_deg'])))}")
    layers = [(L.get("clock_layer") or L["name"], int(L["cadence_min"]), L.get("label_ar", L["id"]))
              for L in cfg["eumetview"]["fire_layers"] if L.get("enabled", True)]
    cl = cfg["eumetview"]["clip_layer"]
    layers.append((cl["name"], int(cl["cadence_min"]), "صور المقاطع"))
    for name, cad, label in layers:
        t = latest_time(wms, S.state, name, cad, pbox)
        if t:
            age = (utcnow() - t).total_seconds() / 60
            ok = "✓" if age <= STALE_HOURS * 60 else "✗ قديمة"
            print(f"  {ok} {label}: آخر صورة قبل {age:.0f} دقيقة ({name})")
        else:
            print(f"  ✗ {label}: غير متاحة ({name})")
    if cfg["firms"]["map_key"]:
        n = len(firms_fetch(session, dict(cfg, firms=dict(cfg["firms"], sources=cfg["firms"]["sources"][:1])),
                            box, 1))
        print(f"  ✓ ناسا: {n} رصدة خلال 24 ساعة")
    else:
        print("  - ناسا: لا يوجد مفتاح FIRMS (اختياري لكنه مستحسن)")
    age, in_progress = geodata_status(cfg)
    print("  قاعدة المنشآت: " + ("قيد التحميل" if in_progress else "غير موجودة، شغّل build-geo"
                                  if age is None else f"عمرها {age:.0f} يوم"))
    print("  تلغرام: " + ("جاهز" if telegram_ready(cfg) else "غير مضبوط"))
    print(f"  حوادث محفوظة: {len(S.incidents)}، خلايا ذاكرة: {len(S.memory)}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="rasad", description="Rasad: satellite fire monitoring")
    ap.add_argument("-c", "--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    ap.add_argument("--state", help="state folder (overrides config)")
    ap.add_argument("--site", help="published app folder (overrides config)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="check data sources")
    p = sub.add_parser("cycle", help="one monitoring cycle")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--alert-now", action="store_true", help="alert even during the learning day")
    p.add_argument("--auto-geo", action="store_true", help="(re)build the facility database when old")
    p.add_argument("--geo-budget", type=float, default=240, help="seconds per run for the facility map")
    p = sub.add_parser("loop", help="monitor continuously on this computer")
    p.add_argument("--interval", type=int, default=10)
    p.add_argument("--no-send", action="store_true")
    sub.add_parser("build-geo", help="download facilities and places (weekly)")
    p = sub.add_parser("clip", help="thermal clip for any point")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--time", help="event time UTC, e.g. 2026-11-01T21:30Z")
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("probe", help="what each satellite saw at a place and period (checks a known fire)")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--start", required=True, help="UTC, e.g. 2026-11-01T19:00Z")
    p.add_argument("--end", required=True, help="UTC, e.g. 2026-11-01T23:00Z")
    p.add_argument("--radius", type=float, default=15.0, help="km around the point")
    p.add_argument("--out", default="probe-out")
    sub.add_parser("test-telegram")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = load_config(args.config, args.state, args.site)

    if args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "cycle":
        run_cycle(cfg, send=not args.no_send, alert_now=args.alert_now)  # watching comes first
        if args.auto_geo and geo_needed(cfg):
            build_geodata(cfg, make_session(), budget_s=args.geo_budget)
    elif args.cmd == "loop":
        while True:
            t0 = time.time()
            try:
                run_cycle(cfg, send=not args.no_send)
                if geo_needed(cfg):
                    build_geodata(cfg, make_session(), budget_s=240)
            except Exception:  # noqa: BLE001
                log.exception("cycle failed")
            time.sleep(max(30.0, args.interval * 60 - (time.time() - t0)))
    elif args.cmd == "probe":
        from .probe import probe
        probe(cfg, args.lat, args.lon, parse_time(args.start), parse_time(args.end), args.radius, args.out)
    elif args.cmd == "build-geo":
        build_geodata(cfg, make_session())
    elif args.cmd == "clip":
        from .clip import make_clip
        from .notify import deliver
        session = make_session()
        wms = WMS(cfg["eumetview"]["wms_url"], session)
        S = State(cfg)
        L = cfg["eumetview"]["clip_layer"]
        cad = int(L["cadence_min"])
        pbox = [args.lon - 0.2, args.lat - 0.2, args.lon + 0.2, args.lat + 0.2]
        latest = latest_time(wms, S.state, L["name"], cad, pbox)
        if latest is None:
            sys.exit("no thermal images available")
        t_end = latest
        if args.time:
            after = int(cfg["clip"].get("frames_after", 2))
            t_end = min(latest, floor_time(parse_time(args.time), cad) + dt.timedelta(minutes=cad * after))
        base = Path(cfg["_site_dir"]) / "clips" / f"manual_{args.lat:.3f}_{args.lon:.3f}_{t_end:%Y%m%d%H%M}"
        video, snap = make_clip(cfg, wms, args.lat, args.lon, t_end, base)
        print(f"Saved: {video}")
        if args.send:
            deliver(cfg, session, f"🛰 صور حرارية حتى {iso(t_end)}\n<code>{args.lat:.4f}, {args.lon:.4f}</code>",
                    video, True)
    elif args.cmd == "test-telegram":
        ok = telegram_ready(cfg) and tg_send(cfg, make_session(), "✅ رصد: التنبيهات تصل إلى هنا.")
        print("Telegram OK" if ok else "Telegram NOT working: check bot token / chat id")


if __name__ == "__main__":
    main()
