"""Two-panel clip like the channel videos: the heat picture on the left, the natural-colour
picture on the right, the place and time above, the coordinates below, and the channel logo
floating softly over the heat picture."""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from .util import log

NEAREST = getattr(Image, "Resampling", Image).NEAREST
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
ROOT = Path(__file__).resolve().parent.parent

# black -> deep red -> orange -> yellow-white: the usual look of the fire picture at night
FIRE = [(0.00, (4, 0, 2)), (0.35, (22, 2, 4)), (0.50, (48, 4, 6)), (0.62, (150, 20, 4)),
        (0.74, (235, 90, 0)), (0.86, (255, 175, 30)), (1.00, (255, 252, 215))]
CYAN = (58, 208, 230, 255)

_RAQM = features.check("raqm")
_FONT_CACHE = {}


def _font(size, bold=False):
    key = (size, bold)
    if key not in _FONT_CACHE:
        names = (["fonts/NotoSansArabic-Bold.ttf", "fonts/NotoSansArabic.ttf"] if bold else
                 ["fonts/NotoSansArabic-Regular.ttf", "fonts/NotoSansArabic.ttf"])
        cands = [str(ROOT / n) for n in names] + [
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        font = None
        for c in cands:
            try:
                font = ImageFont.truetype(c, size, layout_engine=ImageFont.Layout.RAQM if _RAQM
                                          else ImageFont.Layout.BASIC)
                if c.endswith("NotoSansArabic.ttf"):  # variable font: pick the plain or bold instance
                    try:
                        want = b"Bold" if bold else b"Regular"
                        names = [n for n in font.get_variation_names() if want in n
                                 and not any(x in n for x in (b"Condensed", b"Semi", b"Extra", b"Expanded"))]
                        if names:
                            font.set_variation_by_name(names[0])
                    except Exception:  # noqa: BLE001
                        pass
                break
            except OSError:
                continue
        _FONT_CACHE[key] = font or ImageFont.load_default()
    return _FONT_CACHE[key]


def _shape(text):
    """Arabic text ready to draw: with Raqm Pillow shapes it itself; otherwise reshape by hand."""
    if _RAQM:
        return text, {"direction": "rtl", "language": "ar"}
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text)), {}
    except Exception:  # noqa: BLE001
        return text, {}


def _centered(d, W, y, text, font, fill, stroke=0, rtl=True):
    t, kw = _shape(text) if rtl else (text, {"direction": "ltr"} if _RAQM else {})
    box = d.textbbox((0, 0), t, font=font, **kw)
    x = (W - (box[2] - box[0])) // 2 - box[0]
    d.text((x, y), t, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0), **kw)
    return box[3] - box[1]


def _lut(stops):
    xs = np.linspace(0, 1, 256)
    p = [s for s, _ in stops]
    lut = np.zeros((256, 3), np.uint8)
    for c in range(3):
        lut[:, c] = np.round(np.interp(xs, p, [col[c] for _, col in stops]))
    return lut


def _ring(d, cx, cy, r):
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline=(0, 0, 0, 200), width=7)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=CYAN, width=3)


def _logo_frames(path, width):
    try:
        lg = Image.open(path).convert("RGBA")
    except OSError:
        return None
    h = int(lg.height * width / lg.width)
    lg = lg.resize((width, h), LANCZOS)
    # a soft dark halo keeps the white letters readable on bright pixels
    a = lg.getchannel("A").filter(ImageFilter.GaussianBlur(4))
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    shadow.putalpha(a.point(lambda v: int(v * 0.55)))
    return lg, shadow


def _logo_at(canvas, logo, k, n, cx, top, base_opacity):
    """Logo for output frame k of n: floats gently, breathes, and a light sheen passes over it."""
    lg, shadow = logo
    ph = 2 * math.pi * k / max(n, 1)
    dy = int(round(6 * math.sin(ph)))
    op = base_opacity * (0.82 + 0.18 * math.sin(ph * 2))
    x, y = cx - lg.width // 2, top + dy
    sh = shadow.copy()
    sh.putalpha(sh.getchannel("A").point(lambda v: int(v * op)))
    canvas.alpha_composite(sh, (x + 2, y + 3))
    l2 = lg.copy()
    # sheen: a bright diagonal band sweeping left to right once per loop
    w, h = l2.size
    band = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(band)
    pos = int((k / max(n - 1, 1)) * (w + h) * 1.4) - h
    bd.polygon([(pos, 0), (pos + w // 7, 0), (pos + w // 7 - h, h), (pos - h, h)], fill=110)
    band = band.filter(ImageFilter.GaussianBlur(12))
    bright = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    bright.putalpha(Image.fromarray((np.asarray(band, np.float32) / 255 *
                                     np.asarray(l2.getchannel("A"), np.float32)).astype(np.uint8)))
    l2 = Image.alpha_composite(l2, bright)
    l2.putalpha(l2.getchannel("A").point(lambda v: int(v * op)))
    canvas.alpha_composite(l2, (x, y))


def render(cfg, wms, lat, lon, t_end, base, fire_layer, context_layer, frames, info=None):
    """Make the two-panel clip. Returns (video, snapshot). Raises NotVisible via the caller's check."""
    from .clip import NotVisible, _write
    from .wms import coverage
    C = cfg["clip"]
    W, Ht = int(C.get("two_w", 1920)), int(C.get("two_h", 1170))
    top_h, foot_h, gap = 150, 150, 2
    PW, PH = (W - gap) // 2, Ht - top_h - foot_h
    half_h = float(C["half_size_km"])
    half_w = half_h * PW / PH
    px_km = float(C.get("px_km", 0.25))
    nx, ny = max(64, int(round(2 * half_w / px_km))), max(64, int(round(2 * half_h / px_km)))
    dlat = half_h / 111.32
    dlon = half_w / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
    cad = int(fire_layer["cadence_min"])
    shots = []
    for k in range(frames - 1, -1, -1):
        t = t_end - dt.timedelta(minutes=cad * k)
        img = None
        for _attempt in range(2):
            img, actual = wms.getmap(fire_layer["name"], bbox, nx, ny, t=t, style=fire_layer.get("style", ""),
                                     quiet=True)
            if img is not None:
                break
        if img is None or coverage(img) < 0.5:
            continue
        ctx = None
        if context_layer:
            cimg, _ = wms.getmap(context_layer["name"], bbox, nx, ny, t=t, quiet=True)
            if cimg is not None and coverage(cimg) >= 0.5:
                ctx = np.asarray(cimg)[..., :3]
        g = np.asarray(img).astype(np.float32)[..., :3].mean(axis=2)
        if not fire_layer.get("hot_is_bright", True):
            g = 255.0 - g
        if shots and np.array_equal(g, shots[-1][1]):
            continue
        shots.append((actual or t, g, ctx))
    if len(shots) < 2:
        raise RuntimeError(f"only {len(shots)} usable picture(s) from {fire_layer['name']}")
    # colour scale: the usual warmth stays dark, the fire goes orange to white
    base_img = shots[0][1]
    lo = float(np.percentile(base_img, 1))
    hi = max(float(np.percentile(base_img, 99.5)), lo + 4)
    cy0, cy1 = ny // 2 - ny // 8, ny // 2 + ny // 8 + 1
    cx0, cx1 = nx // 2 - nx // 8, nx // 2 + nx // 8 + 1
    centre = [float(np.nanmax(v[cy0:cy1, cx0:cx1])) for _, v, _ in shots]
    change = max(centre) - centre[0]
    contrast = max(c - float(np.nanmedian(v)) for c, (_, v, _) in zip(centre, shots))
    if change < float(C.get("min_visible", 4)) and contrast < float(C.get("min_contrast", 15)):
        raise NotVisible("the fire does not show in these pictures")
    top = max(hi + float(C.get("min_span", 6)), max(centre))
    lut = _lut(FIRE)
    # fonts and fixed texts
    f1, f2, f3 = _font(40, True), _font(29), _font(21)
    off = float(cfg["local_time"]["offset_hours"])
    tzname = cfg["local_time"].get("name", "")
    where = (info or {}).get("where") or f"{lat:.4f}, {lon:.4f}"
    sub = " • ".join(x for x in ((f"منطقة المراقبة: {info['region']}" if info and info.get("region") else ""),
                                  (f"الحكم: {info['verdict']}" if info and info.get("verdict") else "")) if x)
    logo = None
    lp = cfg.get("watermark_logo")
    if lp:
        p = Path(lp)
        logo = _logo_frames(p if p.is_absolute() else ROOT / p, int(C.get("logo_width", 330)))
    sat_fps = int(C.get("fps", 3))
    out_fps = int(C.get("out_fps", 15))
    rep = max(1, out_fps // sat_fps)
    seq = shots + [shots[-1]] * int(C.get("hold_last", 4))
    n_out = len(seq) * rep
    r = int(C.get("ring_px", 40))
    out, best = [], None
    for i, (t, v, ctx) in enumerate(seq):
        x = np.where(v <= hi, 0.5 * (v - lo) / (hi - lo), 0.5 + 0.5 * (v - hi) / (top - hi))
        left = Image.fromarray(lut[np.clip(np.round(x * 255), 0, 255).astype(np.uint8)]).resize((PW, PH), NEAREST)
        right = (Image.fromarray(ctx).resize((PW, PH), NEAREST) if ctx is not None
                 else Image.new("RGB", (PW, PH), (12, 16, 28)))
        frame = Image.new("RGBA", (W, Ht), (0, 0, 0, 255))
        frame.paste(left, (0, top_h))
        frame.paste(right, (PW + gap, top_h))
        d = ImageDraw.Draw(frame)
        loc = t + dt.timedelta(hours=off)
        y = 14
        y += _centered(d, W, y, where, f1, (255, 255, 255)) + 18
        y += _centered(d, W, y, f"{loc:%Y/%m/%d} • {loc:%H:%M} بتوقيت {tzname}", f2, (64, 196, 236)) + 12
        if sub:
            _centered(d, W, y, sub, f3, (150, 158, 172))
        for px in (PW // 2, PW + gap + PW // 2):
            _ring(d, px, top_h + PH // 2, r)
        fy = top_h + PH + 18
        _centered(d, W, fy, f"{lat:.4f}, {lon:.4f}", f2, (255, 255, 255), rtl=False)
        _centered(d, W, fy + 46, "يسار: صورة الحرارة • يمين: الصورة الطبيعية • الأقمار ترى الحرارة لا سببها",
                  f3, (150, 158, 172))
        _centered(d, W, fy + 84, "© EUMETSAT", f3, (120, 128, 140), rtl=False)
        # scale bar on the heat picture
        km_per_px = 2 * half_w / PW
        bar_km = max([s for s in (2, 5, 10, 20) if s <= (2 * half_w) / 4] or [1])
        bx, by = 22, top_h + PH - 30
        d.rectangle((bx, by, bx + int(bar_km / km_per_px), by + 6), fill=(255, 255, 255, 230))
        t_bar, kw = _shape(f"{bar_km} كم")
        d.text((bx, by - 34), t_bar, font=f3, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0), **kw)
        for j in range(rep):
            fr = frame.copy()
            if logo:
                _logo_at(fr, logo, i * rep + j, n_out, PW // 2, top_h + PH // 2 + r + 34,
                         float(C.get("logo_opacity", 0.6)))
            out.append(fr.convert("RGB"))
        if best is None or centre[min(i, len(centre) - 1)] >= best[0]:
            best = (centre[min(i, len(centre) - 1)], len(out) - 1)
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    snap = Path(f"{base}.jpg")
    out[best[1]].save(snap, quality=90)
    video = _write(out, base, out_fps, str(C.get("format", "mp4")))
    log.info("two-panel clip: %d pictures, %d frames", len(shots), len(out))
    return video, snap
