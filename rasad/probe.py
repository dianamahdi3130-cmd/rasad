"""Look back at one place and period: what each satellite saw there.
Used to check a known fire (for example one a news channel showed) against Rasad's sources."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import numpy as np

from .clip import make_clip
from .sources import firms_fetch, pick_frp
from .util import (bbox_px, box_around, floor_time, haversine_km, iso, log, make_session, parse_time,
                   px_to_latlon)
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


IR_LAYERS = [("msg_iodc:ir039", 15, "Meteosat IODC IR3.9"), ("mtg_fd:ir38_hrfi", 10, "Meteosat-12 IR3.8")]


def _scan(wms, name, cad, lat, lon, start, end, half_km=40, px=160):
    """Thermal pictures of the area, slot by slot: where does something suddenly stand out?
    Each picture is compared with the usual (median) picture of the period, after removing the
    whole-scene change of the night, so a fire shows as a small strong spot."""
    box = box_around(lat, lon, half_km)
    frames, t, failed, empty = [], floor_time(start, cad), 0, 0
    while t <= end:
        img, actual = wms.getmap(name, box, px, px, t=t, quiet=True)
        if img is None:
            failed += 1
        else:
            a = np.asarray(img)
            if a[..., 3].mean() > 200:
                frames.append((actual or t, a[..., :3].astype(np.float32).mean(axis=2)))
            else:
                empty += 1
        t += dt.timedelta(minutes=cad)
    log.info("%s: %d pictures, %d failed, %d empty", name, len(frames), failed, empty)
    if len(frames) < 3:
        return []
    med = np.median(np.stack([g for _, g in frames]), axis=0)
    rows = []
    for when, g in frames:
        d = g - med
        d -= np.median(d)
        out = {"t": iso(when)}
        for key, arr in (("darker", -d), ("brighter", d)):
            k = int(np.argmax(arr))
            r, c = divmod(k, px)
            plat, plon = px_to_latlon(box, px, px, c, r)
            out[key] = {"dev": round(float(arr.max()), 1), "lat": round(plat, 3), "lon": round(plon, 3),
                        "km": round(haversine_km(lat, lon, plat, plon), 1)}
        rows.append(out)
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

    best = None  # (strength, layer, cadence, time)
    layers = list(IR_LAYERS)
    try:  # find the real name of Meteosat-12's 3.8 micron picture on this server
        url = E["wms_url"].replace("/geoserver/wms", "/geoserver/mtg_fd/wms")
        r = session.get(url, params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
                        timeout=180)
        names = sorted(set(re.findall(r"<Name>((?:mtg_fd:)?[^<:]+)</Name>", r.text)))
        log.info("Meteosat-12 layers on the server: %s", ", ".join(names))
        # no single 3.8 micron picture: the Fire Temperature RGB carries it in its red colour
        ir38 = [n for n in names if re.search(r"ir_?38|3\.?8", n, re.I)] or \
            [n for n in names if "firetemperature" in n]
        if ir38:
            pick = sorted(ir38, key=lambda n: ("hrfi" not in n, len(n)))[0]
            pick = pick if pick.startswith("mtg_fd:") else f"mtg_fd:{pick}"
            layers = [x for x in layers if not x[0].startswith("mtg_fd:")] + [(pick, 10, f"Meteosat-12 {pick}")]
    except Exception as e:  # noqa: BLE001
        log.warning("could not list Meteosat-12 layers: %s", e)
    for name, cad, label in layers:
        rows = _scan(wms, name, cad, lat, lon, start, end)
        report["ir"].append({"layer": name, "rows": rows})
        for r in rows:
            dk, br = r["darker"], r["brighter"]
            flag = " <<<" if max(dk["dev"], br["dev"]) >= 6 else ""
            log.info("   %s  darker %5.1f at %.3f,%.3f (%4.1f km)   brighter %5.1f at %.3f,%.3f (%4.1f km)%s",
                     r["t"], dk["dev"], dk["lat"], dk["lon"], dk["km"], br["dev"], br["lat"], br["lon"],
                     br["km"], flag)
            for side in (dk, br):
                if side["km"] <= 10 and (best is None or side["dev"] > best[0]):
                    best = (side["dev"], name, cad, label, parse_time(r["t"]))

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

    # a clip around the strongest spot found near the point (or the end of the period)
    clips = []
    if best and best[0] >= 6:  # night-time noise over the sea is about 1-2
        _, name, cad, label, when = best
        clips.append((name, cad, label, when + dt.timedelta(minutes=cad * 4), "clip_spot"))
        log.info("strongest spot within 10 km: %s at %s (strength %.1f)", name, iso(when), best[0])
    L = E["clip_layer"]
    clips.append((L["name"], int(L["cadence_min"]), L["label"], end, "clip"))
    for name, cad, label, t_end, base in clips:
        c = dict(cfg, eumetview=dict(E, clip_layer=dict(L, name=name, cadence_min=cad, label=label)))
        try:
            video, snap = make_clip(c, wms, lat, lon, floor_time(min(t_end, end), cad), out / base, frames=16)
            report.setdefault("clips", []).append([video.name, snap.name])
            log.info("clip: %s", video)
        except Exception as e:  # noqa: BLE001
            log.warning("clip %s failed: %s", base, e)
    (out / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def pictures(cfg, lat, lon, start, end, out_dir="probe-out", half_km=25.0, zoom=3):
    """Diagnosis: the sea finder's own pictures around a point, slot by slot, to see what it reacted
    to. One sheet: per slot the Fire Temperature picture, its change in 30 minutes (red warmer, blue
    cooler) and the 10.5 micron change (a cloud changes both, a fire almost only the first); plus a
    log of the finder's specks near the point with their measures."""
    from PIL import Image, ImageDraw
    from . import seawatch as SW
    from .util import latlon_to_px
    C = cfg["seawatch"]
    km, cad = float(C["km_per_px"]), int(C["cadence_min"])
    box = next((b for b in C["boxes"] if b["bbox"][0] <= lon <= b["bbox"][2] and b["bbox"][1] <= lat <= b["bbox"][3]),
               None) or {"id": "diag", "bbox": box_around(lat, lon, 100)}
    W, H = SW.box_size(box, km)
    col, row = latlon_to_px(box["bbox"], W, H, lat, lon)
    col, row, k = int(col), int(row), int(round(half_km / km))
    wms = WMS(cfg["eumetview"]["wms_url"], make_session())
    raw = {}

    def get(layer, t):
        key = (layer, iso(t))
        if key not in raw:
            img, actual = wms.getmap(layer, box["bbox"], W, H, t=t, quiet=True)
            if img is not None and actual is not None and abs((actual - t).total_seconds()) > 120:
                img = None
            raw[key] = None if img is None else np.asarray(img)
        return raw[key]

    def crop(a):
        pad = np.pad(a, ((k, k), (k, k)) + ((0, 0),) * (a.ndim - 2), mode="constant")
        return pad[row:row + 2 * k + 1, col:col + 2 * k + 1]

    def diverging(d, span=12.0):
        x = np.clip(np.nan_to_num(d) / span, -1, 1)
        rgb = np.zeros(d.shape + (3,), np.uint8)
        rgb[..., 0] = (255 * np.clip(x, 0, 1)).astype(np.uint8)
        rgb[..., 2] = (255 * np.clip(-x, 0, 1)).astype(np.uint8)
        rgb[..., 1] = (60 * (1 - np.abs(x))).astype(np.uint8)
        return rgb

    pics = SW.Pictures(wms)
    tiles, t = [], floor_time(start, cad)
    while t <= end:
        cur, old = get(C["layer"], t), get(C["layer"], t - dt.timedelta(minutes=30))
        c10, o10 = get(C["check_layer"], t), get(C["check_layer"], t - dt.timedelta(minutes=30))
        if cur is None or old is None:
            log.info("%s: picture missing", iso(t))
            t += dt.timedelta(minutes=cad)
            continue
        g, go = SW._gray(Image.fromarray(cur)), SW._gray(Image.fromarray(old))
        rise = g - go
        spot = rise - SW.local_mean(rise, max(2, round(5 / km)))
        red = cur[..., 0].astype(np.float32) - old[..., 0].astype(np.float32)
        cr, cs, cred = crop(rise), crop(spot), crop(red)
        cc = k
        near = cs[cc - 6:cc + 7, cc - 6:cc + 7]
        flat = float(np.mean(np.diff(crop(g), axis=1) == 0))      # share of equal neighbours (blockiness)
        log.info("%s: at the point value %.1f, rise %.1f (red %.0f), spot %.1f; strongest spot within 3 km %.1f; "
                 "equal neighbours %.0f%%", iso(t), float(crop(g)[cc, cc]), float(cr[cc, cc]), float(cred[cc, cc]),
                 float(cs[cc, cc]), float(np.nanmax(near)), 100 * flat)
        # the finder itself, on the same pictures
        pics.cache = {}
        spots, notes = SW.find_spots(cfg, pics, box, t)
        for sp in spots or []:
            if haversine_km(sp["lat"], sp["lon"], lat, lon) <= 15:
                log.info("   finder speck %.4f,%.4f (%.1f km): rise %.1f z %.1f z10 %s area %d px", sp["lat"], sp["lon"],
                         haversine_km(sp["lat"], sp["lon"], lat, lon), sp["rise"], sp["z"], sp["z10"], sp["area_px"])
        log.info("   finder notes for the whole box: %s", notes)
        row_imgs = [crop(cur)[..., :3].astype(np.uint8), diverging(cr), diverging(cred, 30.0)]
        if c10 is not None and o10 is not None:
            d10 = c10[..., :3].astype(np.float32).mean(axis=2) - o10[..., :3].astype(np.float32).mean(axis=2)
            row_imgs.append(diverging(crop(d10)))
        tiles.append((t, row_imgs))
        t += dt.timedelta(minutes=cad)
    if not tiles:
        return
    side = (2 * k + 1) * zoom
    sheet = Image.new("RGB", (4 * (side + 6), len(tiles) * (side + 22)), (8, 10, 16))
    d = ImageDraw.Draw(sheet)
    for i, (t, ims) in enumerate(tiles):
        y = i * (side + 22)
        d.text((4, y + 4), f"{t:%H:%M} UTC   picture | change (mean) | change (red, 3.8um) | change 10.5um",
               fill=(220, 220, 220))
        for j, a in enumerate(ims):
            im = Image.fromarray(a).resize((side, side), Image.NEAREST)
            x0 = j * (side + 6)
            sheet.paste(im, (x0, y + 20))
            c = x0 + side // 2
            d.ellipse([c - 4 * zoom, y + 20 + side // 2 - 4 * zoom, c + 4 * zoom, y + 20 + side // 2 + 4 * zoom],
                      outline=(64, 214, 236))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = out / f"pictures_{lat:.3f}_{lon:.3f}.png"
    sheet.save(name)
    log.info("saved %s", name)


def resolution_test(cfg, lat, lon, t, ref_minutes=30, layer=None):
    """How strongly does a small fire show when the picture is asked for at coarser detail?
    (Big areas come back from the server averaged, which can hide a small fire.)"""
    from .util import haversine_km
    session = make_session()
    wms = WMS(cfg["eumetview"]["wms_url"], session)
    layer = layer or cfg["seawatch"]["layer"]
    for half in (30, 150, 300):
        for km in (2.0, 1.25, 1.0, 0.75, 0.5):
            box = box_around(lat, lon, half)
            W = H = int(round(2 * half / km))
            if W * H > 1_500_000:
                continue
            out = []
            for tt in (t, t - dt.timedelta(minutes=ref_minutes)):
                img, _ = wms.getmap(layer, box, W, H, t=tt, quiet=True)
                out.append(None if img is None else np.asarray(img)[..., :3].astype(np.float32).mean(axis=2))
            if any(o is None for o in out):
                log.info("half %3d km, %.2f km/px: picture missing", half, km)
                continue
            rise = out[0] - out[1]
            r, c = np.unravel_index(np.argmax(rise), rise.shape)
            plat, plon = px_to_latlon(box, W, H, c, r)
            cy, cx = H // 2, W // 2
            k = max(1, int(round(5 / km)))
            near = float(rise[cy - k:cy + k + 1, cx - k:cx + k + 1].max())
            log.info("half %3d km, %.2f km/px (%dx%d): rise near the fire %.1f; strongest rise %.1f at %.3f,%.3f "
                     "(%.0f km away)", half, km, W, H, near, float(rise.max()), plat, plon,
                     haversine_km(lat, lon, plat, plon))
