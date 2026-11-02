"""Clip in an intelligence-display style: the heat picture (left) and the natural picture (right)
of the same minutes side by side, with a target reticle, a coordinate grid, the place and times
above, a timeline and the verdict below, and the channel logo moving quickly across the fire
(plus a faint pattern over the whole frame) so it cannot simply be cut or painted out."""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from .util import log

NEAREST = getattr(Image, "Resampling", Image).NEAREST
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
BICUBIC = getattr(Image, "Resampling", Image).BICUBIC
ROOT = Path(__file__).resolve().parent.parent

# black -> deep red -> orange -> yellow-white: the usual look of the fire picture at night
FIRE = [(0.00, (3, 2, 6)), (0.35, (18, 4, 8)), (0.50, (42, 6, 10)), (0.62, (150, 22, 6)),
        (0.74, (236, 92, 0)), (0.86, (255, 178, 34)), (1.00, (255, 252, 218))]
BG = (5, 8, 13)
CYAN = (64, 214, 236)
DIM = (120, 140, 160)
GRID = (64, 214, 236, 46)
VERDICT_COL = {"مؤكد": (235, 64, 64), "مرجّح": (255, 158, 28), "يحتاج تأكيداً": (242, 201, 76)}

_RAQM = features.check("raqm")
_FONTS = {}


def _font(size, kind="ar"):
    key = (size, kind)
    if key in _FONTS:
        return _FONTS[key]
    if kind == "mono":
        cands = ["DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]
    elif kind == "monol":
        cands = ["DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
    else:
        cands = [str(ROOT / "fonts/NotoSansArabic.ttf"),
                 "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
                 "DejaVuSans-Bold.ttf" if kind == "arb" else "DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if kind == "arb" else
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    font = None
    for c in cands:
        try:
            font = ImageFont.truetype(c, size, layout_engine=ImageFont.Layout.RAQM if _RAQM
                                      else ImageFont.Layout.BASIC)
            if c.endswith("NotoSansArabic.ttf"):  # variable font: pick the plain or bold instance
                try:
                    want = b"Bold" if kind == "arb" else b"Regular"
                    names = [n for n in font.get_variation_names() if want in n and not any(
                        x in n for x in (b"Condensed", b"Semi", b"Extra", b"Expanded"))]
                    if names:
                        font.set_variation_by_name(names[0])
                except Exception:  # noqa: BLE001
                    pass
            break
        except OSError:
            continue
    _FONTS[key] = font or ImageFont.load_default()
    return _FONTS[key]


def _ar(text):
    """Arabic text ready to draw (Raqm shapes it; otherwise reshape by hand if possible)."""
    if _RAQM:
        return text, {"direction": "rtl", "language": "ar"}
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text)), {}
    except Exception:  # noqa: BLE001
        return text, {}


def _text(d, xy, text, font, fill, anchor="la", rtl=False, stroke=0):
    t, kw = _ar(text) if rtl else (text, {"direction": "ltr"} if _RAQM else {})
    d.text(xy, t, font=font, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0), **kw)


def _width(d, text, font, rtl=False):
    t, kw = _ar(text) if rtl else (text, {"direction": "ltr"} if _RAQM else {})
    b = d.textbbox((0, 0), t, font=font, **kw)
    return b[2] - b[0]


def _lut(stops):
    xs = np.linspace(0, 1, 256)
    p = [s for s, _ in stops]
    lut = np.zeros((256, 3), np.uint8)
    for c in range(3):
        lut[:, c] = np.round(np.interp(xs, p, [col[c] for _, col in stops]))
    return lut


def _dms(v, pos, neg):
    a = abs(v)
    d = int(a)
    m = int((a - d) * 60)
    s = (a - d - m / 60) * 3600
    return f"{d:02d}°{m:02d}'{s:04.1f}\"{pos if v >= 0 else neg}"


def _brackets(d, x0, y0, x1, y1, col, arm=30, w=3):
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([(x, y), (x + sx * arm, y)], fill=col, width=w)
        d.line([(x, y), (x, y + sy * arm)], fill=col, width=w)


def _reticle(d, cx, cy, r, col):
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline=(0, 0, 0, 160), width=6)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=2)
    for a in range(0, 360, 90):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line([(cx + c * (r - 8), cy + s * (r - 8)), (cx + c * (r + 16), cy + s * (r + 16))], fill=col, width=3)
    for a in (45, 135, 225, 315):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line([(cx + c * (r + 4), cy + s * (r + 4)), (cx + c * (r + 9), cy + s * (r + 9))], fill=col, width=2)


def _grid(d, x0, y0, PW, PH, bbox, f):
    """Faint latitude/longitude lines with their values on the panel edges."""
    w, s, e, n = bbox
    span = max(e - w, n - s)
    step = 0.05 if span < 0.6 else 0.1 if span < 1.2 else 0.25
    lon = math.ceil(w / step) * step
    while lon < e:
        x = x0 + (lon - w) / (e - w) * PW
        d.line([(x, y0), (x, y0 + PH)], fill=GRID, width=1)
        _text(d, (x + 4, y0 + PH - 6), f"{lon:.2f}E", f, (*DIM, 200), anchor="lb")
        lon += step
    lat = math.ceil(s / step) * step
    while lat < n:
        y = y0 + (n - lat) / (n - s) * PH
        d.line([(x0, y), (x0 + PW, y)], fill=GRID, width=1)
        _text(d, (x0 + 6, y - 4), f"{lat:.2f}N", f, (*DIM, 200), anchor="lb")
        lat += step


def _logo(path, width):
    try:
        lg = Image.open(path).convert("RGBA")
    except OSError:
        return None
    lg = lg.resize((width, int(lg.height * width / lg.width)), LANCZOS)
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    shadow.putalpha(lg.getchannel("A").filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.5)))
    return lg, shadow


def _with_opacity(im, op):
    im = im.copy()
    im.putalpha(im.getchannel("A").point(lambda v: int(v * op)))
    return im


def _tile_layer(path, W, H, op):
    """A faint pattern of the logo over the whole frame, turned on a slant (hard to paint out)."""
    try:
        lg = Image.open(path).convert("RGBA")
    except OSError:
        return None
    small = _with_opacity(lg.resize((150, int(lg.height * 150 / lg.width)), LANCZOS), op)
    big = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    sx, sy = small.width + 120, small.height + 110
    for j, y in enumerate(range(0, H * 2, sy)):
        for x in range((j % 2) * sx // 2, W * 2, sx):
            big.alpha_composite(small, (x, y))
    return big.rotate(-22, resample=BICUBIC, expand=False)


def render(cfg, wms, lat, lon, t_end, base, fire_layer, context_layer, frames, info=None):
    """Make the clip. Returns (video, snapshot); raises NotVisible when the fire does not show."""
    from .clip import NotVisible, _write
    from .wms import coverage
    C = cfg["clip"]
    info = info or {}
    W, H = int(C.get("two_w", 1920)), int(C.get("two_h", 1080))
    top_h, foot_h, m, gap = 104, 128, 8, 8
    PW, PH = (W - 2 * m - gap) // 2, H - top_h - foot_h - 2 * m
    LX, RX, PY = m, m + PW + gap, top_h + m
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

    off = float(cfg["local_time"]["offset_hours"])
    tzname = cfg["local_time"].get("name", "")
    first = info.get("first")
    first_t = dt.datetime.fromisoformat(first.replace("Z", "+00:00")) if first else None
    verdict = info.get("verdict") or ""
    vcol = VERDICT_COL.get(verdict, CYAN)
    f_title, f_sub = _font(36, "arb"), _font(22, "ar")
    f_mono, f_monos, f_monoxs = _font(26, "mono"), _font(18, "monol"), _font(15, "monol")
    f_lab, f_lab_s = _font(20, "arb"), _font(18, "ar")

    # what stays the same in every frame: panels' frames, labels, grid, footer coordinates
    static = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(static)
    for x0, label_ar, label_en in ((LX, "حراري", "THERMAL"), (RX, "مرئي", "VISUAL")):
        _grid(d, x0, PY, PW, PH, bbox, f_monoxs)
        _brackets(d, x0 + 6, PY + 6, x0 + PW - 6, PY + PH - 6, CYAN)
        d.rectangle((x0 + 18, PY + 18, x0 + 18 + 190, PY + 52), fill=(0, 0, 0, 150))
        _text(d, (x0 + 28, PY + 35), label_en, f_monos, CYAN, anchor="lm")
        _text(d, (x0 + 198, PY + 35), label_ar, f_lab_s, (230, 236, 244), anchor="rm", rtl=True)
        nxp, nyp = x0 + PW - 44, PY + 34  # north arrow
        d.polygon([(nxp, nyp - 16), (nxp - 9, nyp + 8), (nxp, nyp + 3), (nxp + 9, nyp + 8)], fill=(230, 236, 244))
        _text(d, (nxp, nyp + 12), "N", f_monos, (230, 236, 244), anchor="mt")
        km_per_px = 2 * half_w / PW  # scale bar
        bar_km = max([s for s in (2, 5, 10, 20) if s <= 2 * half_w / 4] or [1])
        bx, by, bl = x0 + 24, PY + PH - 44, int(bar_km / km_per_px)
        d.rectangle((bx - 6, by - 30, bx + bl + 8, by + 14), fill=(0, 0, 0, 140))
        d.line([(bx, by), (bx + bl, by)], fill=(235, 240, 248), width=3)
        for tx in (bx, bx + bl // 2, bx + bl):
            d.line([(tx, by - 7), (tx, by + 7)], fill=(235, 240, 248), width=2)
        _text(d, (bx + bl // 2, by - 12), f"{bar_km} km", f_monoxs, (235, 240, 248), anchor="mb")
    sx0, sy0 = LX + PW - 34, PY + 90  # heat colour scale
    ramp = np.linspace(1, 0, 220)[:, None].repeat(12, axis=1)
    static.paste(Image.fromarray(lut[np.clip((ramp * 255).astype(int), 0, 255)]).convert("RGBA"), (sx0, sy0))
    d.rectangle((sx0 - 1, sy0 - 1, sx0 + 12, sy0 + 220), outline=(*DIM, 220))
    _text(d, (sx0 + 6, sy0 - 6), "HOT", f_monoxs, (255, 200, 120), anchor="mb")
    _text(d, (sx0 + 6, sy0 + 226), "COLD", f_monoxs, DIM, anchor="mt")
    for x0 in (LX, RX):  # target label with a leader line
        cx, cy = x0 + PW // 2, PY + PH // 2
        lx, ly = cx + 70, cy - 70
        d.line([(cx + 40, cy - 40), (lx, ly), (lx + 150, ly)], fill=CYAN, width=2)
        _text(d, (lx + 6, ly - 4), "TGT", f_monos, CYAN, anchor="lb")
        _text(d, (lx + 6, ly + 4), f"{lat:.4f}N {lon:.4f}E", f_monoxs, (230, 236, 244), anchor="lt")
    d.rectangle((0, 0, W, top_h), fill=(*BG, 255))  # top bar
    d.line([(0, top_h - 1), (W, top_h - 1)], fill=(*CYAN, 120), width=1)
    _text(d, (W // 2, 30), info.get("where") or f"{lat:.4f}, {lon:.4f}", f_title, (245, 248, 252),
          anchor="mm", rtl=True)
    sub = " • ".join(x for x in (info.get("region") and f"منطقة المراقبة: {info['region']}",
                                  info.get("facility") and f"أقرب منشأة: {info['facility']}") if x)
    if sub:
        _text(d, (W // 2, 74), sub, f_sub, (160, 172, 188), anchor="mm", rtl=True)
    _text(d, (24, 30), str(C.get("hud_title", "THERMAL WATCH // رصد")), f_monos, CYAN, anchor="lm")
    _text(d, (24, 62), f"{lat:.4f}N  {lon:.4f}E", f_monos, (200, 210, 222), anchor="lm")
    fy = H - foot_h  # bottom bar
    d.rectangle((0, fy, W, H), fill=(*BG, 255))
    d.line([(0, fy), (W, fy)], fill=(*CYAN, 120), width=1)
    _text(d, (24, fy + 34), f"{lat:.4f}°N  {lon:.4f}°E", f_mono, (245, 248, 252), anchor="lm")
    _text(d, (24, fy + 72), f"{_dms(lat, 'N', 'S')}  {_dms(lon, 'E', 'W')}", f_monos, DIM, anchor="lm")
    _text(d, (24, fy + 104), "© EUMETSAT", f_monoxs, (110, 122, 138), anchor="lm")
    if verdict:
        vw = _width(d, verdict, f_lab, rtl=True) + 40
        d.rounded_rectangle((W - 24 - vw, fy + 18, W - 24, fy + 56), radius=8, fill=(*vcol, 235))
        _text(d, (W - 24 - vw // 2, fy + 37), verdict, f_lab, (10, 10, 12), anchor="mm", rtl=True)
        _text(d, (W - 24 - vw - 16, fy + 37), "الحكم", f_sub, (160, 172, 188), anchor="rm", rtl=True)
    if info.get("size"):
        _text(d, (W - 24, fy + 84), f"الحجم: {info['size']}", f_sub, (200, 210, 222), anchor="rm", rtl=True)

    # moving watermark: the logo crosses the fire quickly on a figure-eight path, and a faint
    # slanted pattern of it covers the whole frame
    logo = tiles = None
    lp = cfg.get("watermark_logo")
    if lp:
        p = Path(lp)
        p = p if p.is_absolute() else ROOT / p
        logo = _logo(p, int(C.get("logo_width", 300)))
        if float(C.get("logo_tile_opacity", 0.05)) > 0:
            tiles = _tile_layer(p, W, H, float(C.get("logo_tile_opacity", 0.05)))
            panels = Image.new("L", (W * 2, H * 2), 0)  # the pattern only over the pictures
            pd = ImageDraw.Draw(panels)
            pd.rectangle((LX, PY, LX + PW, PY + PH), fill=255)
            pd.rectangle((RX, PY, RX + PW, PY + PH), fill=255)
            tile_mask = panels.crop((0, 0, W, H))

    sat_fps = int(C.get("fps", 3))
    out_fps = int(C.get("out_fps", 24))
    rep = max(1, out_fps // sat_fps)
    seq = list(range(len(shots))) + [len(shots) - 1] * int(C.get("hold_last", 4))
    period = float(C.get("logo_period_s", 2.4)) * out_fps
    r = int(C.get("ring_px", 44))
    tl_x0, tl_x1, tl_y = 620, W - 620, fy + 64
    out, best = [], None
    for i, si in enumerate(seq):
        t, v, ctx = shots[si]
        x = np.where(v <= hi, 0.5 * (v - lo) / (hi - lo), 0.5 + 0.5 * (v - hi) / (top - hi))
        left = Image.fromarray(lut[np.clip(np.round(x * 255), 0, 255).astype(np.uint8)]).resize((PW, PH), NEAREST)
        right = (Image.fromarray(ctx).resize((PW, PH), NEAREST) if ctx is not None
                 else Image.new("RGB", (PW, PH), (12, 16, 28)))
        frame = Image.new("RGBA", (W, H), (*BG, 255))
        frame.paste(left, (LX, PY))
        frame.paste(right, (RX, PY))
        frame.alpha_composite(static)
        d = ImageDraw.Draw(frame)
        for x0 in (LX, RX):
            _reticle(d, x0 + PW // 2, PY + PH // 2, r, CYAN)
        loc = t + dt.timedelta(hours=off)  # times: Baghdad and UTC, minutes since the first sighting
        _text(d, (W - 24, 30), f"{loc:%Y-%m-%d  %H:%M}  {tzname}", f_monos, (245, 248, 252), anchor="rm")
        rel = ""
        if first_t:
            mins = int(round((t - first_t).total_seconds() / 60))
            rel = f"   T{'+' if mins >= 0 else '-'}{abs(mins)}m"
        _text(d, (W - 24, 62), f"UTC {t:%H:%MZ}{rel}", f_monos, CYAN, anchor="rm")
        n_sh = len(shots)  # timeline of the pictures, the current one lit
        for j in range(n_sh):
            tx = tl_x0 + (tl_x1 - tl_x0) * (j / max(n_sh - 1, 1))
            on = j == si
            d.line([(tx, tl_y - (14 if on else 8)), (tx, tl_y + (14 if on else 8))],
                   fill=(*(CYAN if on else DIM), 255), width=4 if on else 2)
        d.line([(tl_x0, tl_y), (tl_x1, tl_y)], fill=(*DIM, 160), width=1)
        for j in (0, n_sh - 1):
            lt = shots[j][0] + dt.timedelta(hours=off)
            _text(d, (tl_x0 + (tl_x1 - tl_x0) * (j / max(n_sh - 1, 1)), tl_y + 20), f"{lt:%H:%M}", f_monoxs,
                  DIM, anchor="mt")
        if first_t and shots[0][0] <= first_t <= shots[-1][0]:
            span = (shots[-1][0] - shots[0][0]).total_seconds() or 1
            fx = tl_x0 + (tl_x1 - tl_x0) * (first_t - shots[0][0]).total_seconds() / span
            d.polygon([(fx, tl_y - 18), (fx - 8, tl_y - 32), (fx + 8, tl_y - 32)], fill=(*vcol, 255))
            _text(d, (fx, tl_y - 36), "أول رصد", f_lab_s, (*vcol, 255), anchor="mb", rtl=True)
        _text(d, (W // 2, fy + 22), "الخط الزمني للصور", f_lab_s, (160, 172, 188), anchor="mm", rtl=True)
        for j in range(rep):
            k = i * rep + j
            fr = frame.copy()
            dd = ImageDraw.Draw(fr)
            sy = PY + int((k % (out_fps * 2)) / (out_fps * 2) * PH)  # scanning line on the heat panel
            dd.line([(LX, sy), (LX + PW, sy)], fill=(*CYAN, 70), width=2)
            if tiles is not None:
                o = (k * 2) % 300
                layer = tiles.crop((o, o // 2, o + W, o // 2 + H))
                layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel("A")),
                                                          np.asarray(tile_mask))))
                fr.alpha_composite(layer)
            if logo:
                lg, sh = logo
                ph = 2 * math.pi * k / period
                cx = LX + PW // 2 + int(PW * 0.30 * math.sin(ph))
                cy = PY + PH // 2 + int(PH * 0.16 * math.sin(2 * ph))
                op = float(C.get("logo_opacity", 0.55)) * (0.85 + 0.15 * math.sin(3 * ph))
                fr.alpha_composite(_with_opacity(sh, op), (cx - lg.width // 2 + 2, cy - lg.height // 2 + 3))
                fr.alpha_composite(_with_opacity(lg, op), (cx - lg.width // 2, cy - lg.height // 2))
            out.append(fr.convert("RGB"))
        if best is None or centre[si] > best[0]:
            best = (centre[si], len(out) - rep)
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    snap = Path(f"{base}.jpg")
    out[best[1]].save(snap, quality=90)
    video = _write(out, base, out_fps, str(C.get("format", "mp4")))
    log.info("clip: %d pictures, %d frames", len(shots), len(out))
    return video, snap
