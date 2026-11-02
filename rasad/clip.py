"""Channel-style thermal loops: frames before and after the fire, coastline, circle, labels."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .util import box_around, log
from .wms import coverage

NEAREST = getattr(Image, "Resampling", Image).NEAREST
PALETTE = [(0.00, (0, 0, 38)), (0.20, (14, 0, 50)), (0.34, (50, 0, 40)), (0.48, (92, 0, 24)),
           (0.56, (140, 0, 14)), (0.66, (215, 35, 0)), (0.78, (255, 115, 0)),
           (0.90, (255, 195, 40)), (1.00, (255, 250, 205))]
FONT_FILES = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def _lut():
    xs = np.linspace(0, 1, 256)
    stops = [p for p, _ in PALETTE]
    lut = np.zeros((256, 3), np.uint8)
    for c in range(3):
        lut[:, c] = np.round(np.interp(xs, stops, [col[c] for _, col in PALETTE]))
    return lut


def _font(size):
    for name in FONT_FILES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _erode(m):
    e = m.copy()
    e[1:, :] &= m[:-1, :]
    e[:-1, :] &= m[1:, :]
    e[:, 1:] &= m[:, :-1]
    e[:, :-1] &= m[:, 1:]
    return e


def _hot_is_bright(shots, c, r=6):
    g0, g1 = shots[0][1], shots[-1][1]
    sl = (slice(max(0, c - r), c + r + 1), slice(max(0, c - r), c + r + 1))
    up = float(g1[sl].max() - g0[sl].max())
    down = float(g0[sl].min() - g1[sl].min())
    return None if max(up, down) < 10 else up > down


def _decorate(im, cfg, t, lat, lon, km_per_px, label, fonts):
    big, small, wm = fonts
    OUT = im.size[0]
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    c, R = OUT // 2, OUT // 17
    d.ellipse((c - R - 2, c - R - 2, c + R + 2, c + R + 2), outline=(0, 0, 0, 255), width=9)
    d.ellipse((c - R, c - R, c + R, c + R), outline=(70, 225, 255, 255), width=5)
    pad = max(10, OUT // 64)
    stroke = {"stroke_width": 2, "stroke_fill": (0, 0, 0, 255)}
    off = float(cfg["local_time"]["offset_hours"])
    loc = t + dt.timedelta(hours=off)
    line1 = f"{t:%Y-%m-%d %H:%M} UTC   ({loc:%H:%M} UTC{off:+g})"
    d.text((pad, pad), line1, font=big, fill=(255, 255, 255, 255), **stroke)
    h1 = d.textbbox((0, 0), line1, font=big)[3]
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    d.text((pad, pad + h1 + 8), f"{label}   {abs(lat):.3f}{ns}  {abs(lon):.3f}{ew}", font=small,
           fill=(225, 225, 225, 255), **stroke)
    total_km = km_per_px * OUT
    bar_km = max([v for v in (2, 5, 10, 20, 25, 50, 100) if v <= total_km / 4] or [1])
    bar_px = int(bar_km / km_per_px)
    y0 = OUT - pad - 12
    d.rectangle((pad, y0, pad + bar_px, y0 + 7), fill=(255, 255, 255, 235), outline=(0, 0, 0, 255))
    lh = d.textbbox((0, 0), "0 km", font=small)[3]
    d.text((pad, y0 - lh - 8), f"{bar_km:g} km", font=small, fill=(255, 255, 255, 255), **stroke)
    credit = "© EUMETSAT"
    cw = d.textbbox((0, 0), credit, font=small)[2]
    d.text((OUT - pad - cw, OUT - pad - lh - 4), credit, font=small, fill=(255, 255, 255, 220), **stroke)
    text = str(cfg.get("watermark") or "").strip()
    if text:
        bw = d.textbbox((0, 0), text, font=wm)[2]
        d.text((c - bw // 2, c + R + 12), text, font=wm, fill=(255, 255, 255, 120))
    return Image.alpha_composite(im.convert("RGBA"), over).convert("RGB")


def _write(frames, base, fps, fmt):
    if fmt == "mp4":
        try:
            import imageio.v2 as iio
            p = Path(f"{base}.mp4")
            with iio.get_writer(str(p), fps=fps, codec="libx264", pixelformat="yuv420p", quality=7,
                                macro_block_size=16, ffmpeg_log_level="error") as w:
                for f in frames:
                    w.append_data(np.asarray(f.convert("RGB")))
            return p
        except Exception as e:  # noqa: BLE001
            log.warning("MP4 writer unavailable (%s); writing a GIF.", e)
    p = Path(f"{base}.gif")
    adaptive = getattr(Image, "Palette", Image).ADAPTIVE
    pal = [f.convert("P", palette=adaptive, colors=128) for f in frames]
    pal[0].save(p, save_all=True, append_images=pal[1:], duration=int(1000 / fps), loop=0)
    return p


def make_clip(cfg, wms, lat, lon, t_end, base, frames=None, half_km=None):
    """Loop of the thermal channel around (lat, lon) ending at t_end. Returns (video, snapshot)."""
    C, L = cfg["clip"], cfg["eumetview"]["clip_layer"]
    cad, n = int(L["cadence_min"]), int(frames or C["frames"])
    half = float(half_km or C["half_size_km"])
    N = int(C["native_px"])
    scale = max(1, int(C["out_px"]) // N)
    OUT = N * scale
    bbox = box_around(lat, lon, half)
    shots, seen = [], set()
    for k in range(n - 1, -1, -1):
        t = t_end - dt.timedelta(minutes=cad * k)
        img, actual = wms.getmap(L["name"], bbox, N, N, t=t, style=L.get("style", ""), quiet=True)
        if img is None or coverage(img) < 0.5:
            continue
        g = np.asarray(img).astype(np.float32)[..., :3].mean(axis=2)
        h = hashlib.md5(g.tobytes()).hexdigest()
        if h not in seen:
            seen.add(h)
            shots.append((actual or t, g))
    if len(shots) < 2:
        raise RuntimeError(f"only {len(shots)} usable image(s) from {L['name']}")
    hot = L.get("hot_is_bright", "auto")
    if hot in ("auto", None):
        hot = _hot_is_bright(shots, N // 2)
        if hot is None:
            hot = bool(L.get("hot_is_bright_fallback", False))
    vals = [(t, g if hot else 255.0 - g) for t, g in shots]
    base_img = vals[0][1]
    lo = float(np.percentile(base_img, 1))
    hi = max(float(np.percentile(base_img, 99.5)), lo + 4)
    top = max(255.0, hi + 1)
    lut = _lut()
    edges, m = None, None
    from .geodb import land_mask_points
    mask = land_mask_points()
    if mask is not None:  # 1 km land mask, good enough for a coastline outline
        w, s, e, n = bbox
        lons = w + (np.arange(N) + 0.5) / N * (e - w)
        lats = n - (np.arange(N) + 0.5) / N * (n - s)
        gl, go = np.meshgrid(lats, lons, indexing="ij")
        m = mask(gl.ravel(), go.ravel()).reshape(N, N)
    else:
        land_layer = cfg["eumetview"].get("land_layer")
        limg = wms.getmap(land_layer, bbox, N, N, style=cfg["eumetview"].get("land_style", ""),
                          quiet=True)[0] if land_layer else None
        if limg is not None:
            m = np.asarray(limg)[..., 3] > 0
    if m is not None and 0 < m.mean() < 1:
        edge = m & ~_erode(m)
        edges = Image.fromarray((edge * 255).astype(np.uint8)).resize((OUT, OUT), NEAREST)
    fonts = (_font(max(18, OUT // 40)), _font(max(14, OUT // 56)), _font(max(28, OUT // 16)))
    km_per_px = 2 * half / OUT
    out = []
    for t, v in vals:
        x = np.where(v <= hi, 0.5 * (v - lo) / (hi - lo), 0.5 + 0.5 * (v - hi) / (top - hi))
        im = Image.fromarray(lut[np.clip(np.round(x * 255), 0, 255).astype(np.uint8)]).resize((OUT, OUT), NEAREST)
        if edges is not None:
            im.paste((235, 235, 235), (0, 0, OUT, OUT), edges)
        out.append(_decorate(im, cfg, t, lat, lon, km_per_px, L["label"], fonts))
    out += [out[-1]] * int(C.get("hold_last", 4))
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    snap = Path(f"{base}.jpg")
    out[-1].save(snap, quality=85)
    return _write(out, base, int(C.get("fps", 3)), str(C.get("format", "mp4"))), snap
