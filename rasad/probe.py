"""Look back at one place and period: what each satellite saw there.
Used to check a known fire (for example one a news channel showed) against Rasad's sources."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from .clip import make_clip
from .sources import firms_fetch, pick_frp
from .util import bbox_px, box_around, floor_time, haversine_km, iso, log, make_session, px_to_latlon
from .wms import WMS, components


def _geo_series(wms, L, lat, lon, start, end, radius_km):
    cad = int(L["cadence_min"])
    box = box_around(lat, lon, 50)
    W, H = bbox_px(box, 300)
    rows, t = [], floor_time(start, cad)
    while t <= end:
        img, actual = wms.getmap(L["name"], box, W, H, t=t, style=L.get("style", ""), quiet=True)
        row = {"asked": iso(t), "served": iso(actual) if actual else None}
        if img is None:
            row["error"] = True
        else:
            mask = np.asarray(img)[..., 3] > 40
            fires = []
            for pts in components(mask):
                r, c = float(pts[:, 0].mean()), float(pts[:, 1].mean())
                flat, flon = px_to_latlon(box, W, H, c, r)
                km = haversine_km(lat, lon, flat, flon)
                if km <= radius_km:
                    frp = pick_frp(wms.feature_info(L["name"], box, W, H, c, r, t=t))
                    fires.append({"lat": round(flat, 4), "lon": round(flon, 4), "km": round(km, 1),
                                  "frp": frp})
            row["fires"] = fires
        rows.append(row)
        t += dt.timedelta(minutes=cad)
    return rows


def _ir_series(wms, L, lat, lon, start, end):
    """Brightness of the thermal picture at the point versus its surroundings, slot by slot."""
    cad = int(L["cadence_min"])
    box = box_around(lat, lon, 25)
    rows, t = [], floor_time(start, cad)
    while t <= end:
        img, actual = wms.getmap(L["name"], box, 50, 50, t=t, style=L.get("style", ""), quiet=True)
        if img is not None:
            g = np.asarray(img).astype(np.float32)[..., :3].mean(axis=2)
            core = g[22:29, 22:29]
            med = float(np.median(g))
            rows.append({"asked": iso(t), "served": iso(actual) if actual else None,
                         "brighter": round(float(core.max()) - med, 1),
                         "darker": round(med - float(core.min()), 1)})
        else:
            rows.append({"asked": iso(t), "error": True})
        t += dt.timedelta(minutes=cad)
    return rows


def probe(cfg, lat, lon, start, end, radius_km=15.0, out_dir="probe-out"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    E = cfg["eumetview"]
    report = {"lat": lat, "lon": lon, "start": iso(start), "end": iso(end), "radius_km": radius_km,
              "geo": {}, "ir": [], "nasa": []}

    for L in E["fire_layers"]:
        rows = _geo_series(wms, L, lat, lon, start, end, radius_km)
        report["geo"][L["id"]] = rows
        hits = [r for r in rows if r.get("fires")]
        log.info("%s: %d images, %d failed, fire symbols near the point in %d", L["name"], len(rows),
                 sum(1 for r in rows if r.get("error")), len(hits))
        for r in hits:
            log.info("   %s (served %s): %s", r["asked"], r["served"],
                     "; ".join(f"{f['km']} km, {f['frp']} MW" for f in r["fires"]))

    L = E["clip_layer"]
    report["ir"] = _ir_series(wms, L, lat, lon, start, end)
    for r in report["ir"]:
        if not r.get("error"):
            log.info("   IR3.9 %s (served %s): point vs surroundings  brighter %+6.1f  darker %+6.1f",
                     r["asked"], r["served"], r["brighter"], r["darker"])

    day = start.date()
    while day <= end.date():
        for d in firms_fetch(session, cfg, box_around(lat, lon, radius_km * 2), 1, date=day.isoformat()):
            km = haversine_km(lat, lon, d["lat"], d["lon"])
            if km <= radius_km:
                d["km"] = round(km, 1)
                report["nasa"].append(d)
        day += dt.timedelta(days=1)
    report["nasa"].sort(key=lambda d: d["t"])
    for d in report["nasa"]:
        log.info("   NASA %s %s: %.4f,%.4f  %s MW  conf=%s night=%s  (%s km)", d["t"], d["src"], d["lat"],
                 d["lon"], d.get("frp"), d.get("conf"), d.get("night"), d["km"])
    if not report["nasa"]:
        log.info("   NASA: nothing within %s km", radius_km)

    cad = int(L["cadence_min"])
    frames = max(2, min(16, int((end - start).total_seconds() // (cad * 60)) + 1))
    try:
        video, snap = make_clip(cfg, wms, lat, lon, floor_time(end, cad), out / "clip", frames=frames)
        report["clip"] = [video.name, snap.name]
        log.info("clip: %s (%d frames up to %s)", video, frames, iso(floor_time(end, cad)))
    except Exception as e:  # noqa: BLE001
        log.warning("clip failed: %s", e)
    (out / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
