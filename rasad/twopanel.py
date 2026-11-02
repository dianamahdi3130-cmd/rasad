"""Clip in a clean monitoring-display style: the heat picture (left) and the natural picture
(right) of the same minutes side by side, the place and time above, the coordinates, a timeline
with the time of the first sighting and the verdict below. When the fire first appears, a line
sweeps down once and reveals the new picture over the old one (before / after). The channel logo
glides slowly across the fire so it cannot simply be cropped out."""
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
FIRE = [(0.00, (3, 2, 6)), (0.35, (18, 4, 8)), (0.50, (42, 6, 10)), (0.62, (150, 22, 6)),
        (0.74, (236, 92, 0)), (0.86, (255, 178, 34)), (1.00, (255, 252, 218))]
BG = (6, 9, 14)
CYAN = (64, 214, 236)
WHITE = (240, 244, 250)
DIM = (128, 142, 160)
VERDICT_COL = {"مؤكد": (232, 62, 62), "مرجّح": (255, 158, 28), "يحتاج تأكيداً": (242, 201, 76)}

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


def _text(d, xy, text, font, fill, anchor="la", rtl=False):
    t, kw = _ar(text) if rtl else (text, {"direction": "ltr"} if _RAQM else {})
    d.text(xy, t, font=font, fill=fill, anchor=anchor, **kw)


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


def _brackets(d, x0, y0, x1, y1, col, arm=34, w=3):
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([(x, y), (x + sx * arm, y)], fill=col, width=w)
        d.line([(x, y), (x, y + sy * arm)], fill=col, width=w)


def _reticle(d, cx, cy, r, col):
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline=(0, 0, 0, 150), width=6)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=3)
    for a in range(0, 360, 90):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line([(cx + c * (r + 6), cy + s * (r + 6)), (cx + c * (r + 18), cy + s * (r + 18))], fill=col, width=3)


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


def render(cfg, wms, lat, lon, t_end, base, fire_layer, context_layer, frames, info=None):
    """Make the clip. Returns (video, snapshot); raises NotVisible when the fire does not show."""
    from .clip import NotVisible, _write
    from .wms import coverage
    C = cfg["clip"]
    info = info or {}
    W, H = int(C.get("two_w", 1920)), int(C.get("two_h", 1080))
    top_h, foot_h, m, gap = 112, 120, 10, 10
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
    # the picture where the fire first shows: the before/after line sweeps down there, once
    fire_idx = next((i for i, c in enumerate(centre) if c - centre[0] >= float(C.get("min_visible", 4))), None)

    off = float(cfg["local_time"]["offset_hours"])
    tzname = cfg["local_time"].get("name", "")
    first = info.get("first")
    first_t = dt.datetime.fromisoformat(first.replace("Z", "+00:00")) if first else None
    if first_t is None and fire_idx is not None:
        first_t = shots[fire_idx][0]
    verdict = info.get("verdict") or ""
    vcol = VERDICT_COL.get(verdict, CYAN)
    f_title, f_sub = _font(38, "arb"), _font(23, "ar")
    f_brand, f_clock, f_mono, f_small = _font(28, "mono"), _font(34, "mono"), _font(24, "mono"), _font(17, "monol")
    f_lab, f_badge = _font(20, "ar"), _font(24, "arb")

    # everything that does not change between frames
    static = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(static)
    d.rectangle((0, 0, W, top_h), fill=(*BG, 255))
    d.line([(0, top_h - 1), (W, top_h - 1)], fill=(*CYAN, 90), width=1)
    _text(d, (32, top_h // 2), str(C.get("hud_title", "Alfaqaar313")), f_brand, CYAN, anchor="lm")
    _text(d, (W // 2, 38), info.get("where") or f"{lat:.4f}, {lon:.4f}", f_title, WHITE, anchor="mm", rtl=True)
    if info.get("region"):
        _text(d, (W // 2, 82), info["region"], f_sub, DIM, anchor="mm", rtl=True)
    for x0, label in ((LX, "حراري"), (RX, "مرئي")):
        _brackets(d, x0 + 8, PY + 8, x0 + PW - 8, PY + PH - 8, (*CYAN, 230))
        lw = _width(d, label, f_lab, rtl=True)
        d.rounded_rectangle((x0 + 22, PY + 22, x0 + 22 + lw + 28, PY + 58), radius=6, fill=(0, 0, 0, 150))
        _text(d, (x0 + 36 + lw // 2, PY + 40), label, f_lab, WHITE, anchor="mm", rtl=True)
    km_per_px = 2 * half_w / PW  # one small scale bar on the heat picture
    bar_km = max([s for s in (2, 5, 10, 20) if s <= 2 * half_w / 4] or [1])
    bx, by, bl = LX + 28, PY + PH - 36, int(bar_km / km_per_px)
    d.line([(bx, by), (bx + bl, by)], fill=(*WHITE, 230), width=3)
    for tx in (bx, bx + bl):
        d.line([(tx, by - 6), (tx, by + 6)], fill=(*WHITE, 230), width=2)
    _text(d, (bx + bl // 2, by - 10), f"{bar_km} km", f_small, (*WHITE, 230), anchor="mb")
    fy = H - foot_h
    d.rectangle((0, fy, W, H), fill=(*BG, 255))
    d.line([(0, fy), (W, fy)], fill=(*CYAN, 90), width=1)
    _text(d, (32, fy + 46), f"{lat:.4f}°N   {lon:.4f}°E", f_mono, WHITE, anchor="lm")
    _text(d, (32, fy + 86), "© EUMETSAT", f_small, (100, 112, 128), anchor="lm")
    if verdict:
        vw = _width(d, verdict, f_badge, rtl=True) + 48
        d.rounded_rectangle((W - 32 - vw, fy + 34, W - 32, fy + 82), radius=10, fill=(*vcol, 240))
        _text(d, (W - 32 - vw // 2, fy + 58), verdict, f_badge, (12, 12, 14), anchor="mm", rtl=True)

    # the channel logo glides slowly across the fire, one figure-eight over the whole clip
    logo = None
    lp = cfg.get("watermark_logo")
    if lp:
        p = Path(lp)
        logo = _logo(p if p.is_absolute() else ROOT / p, int(C.get("logo_width", 300)))

    sat_fps = int(C.get("fps", 3))
    out_fps = int(C.get("out_fps", 24))
    rep = max(1, out_fps // sat_fps)
    wipe_n = int(out_fps * float(C.get("wipe_s", 1.0)))
    seq = list(range(len(shots))) + [len(shots) - 1] * int(C.get("hold_last", 4))
    n_out = len(seq) * rep + (wipe_n if fire_idx else 0)
    r = int(C.get("ring_px", 46))
    tl_x0, tl_x1, tl_y = 640, W - 640, fy + 64
    n_sh = len(shots)
    span = (shots[-1][0] - shots[0][0]).total_seconds() or 1

    def tl_x(t):
        return tl_x0 + (tl_x1 - tl_x0) * min(1.0, max(0.0, (t - shots[0][0]).total_seconds() / span))

    def panels(si):
        t, v, ctx = shots[si]
        x = np.where(v <= hi, 0.5 * (v - lo) / (hi - lo), 0.5 + 0.5 * (v - hi) / (top - hi))
        left = Image.fromarray(lut[np.clip(np.round(x * 255), 0, 255).astype(np.uint8)]).resize((PW, PH), NEAREST)
        right = (Image.fromarray(ctx).resize((PW, PH), NEAREST) if ctx is not None
                 else Image.new("RGB", (PW, PH), (12, 16, 28)))
        return left, right

    def chrome(frame, si):
        """Reticles, clock and timeline for picture si."""
        t = shots[si][0]
        d = ImageDraw.Draw(frame)
        for x0 in (LX, RX):
            _reticle(d, x0 + PW // 2, PY + PH // 2, r, CYAN)
        loc = t + dt.timedelta(hours=off)
        _text(d, (W - 32, 40), f"{loc:%H:%M}", f_clock, WHITE, anchor="rm")
        cw = _width(d, f"{loc:%H:%M}", f_clock)
        _text(d, (W - 32 - cw - 14, 42), tzname, f_sub, DIM, anchor="rm", rtl=True)
        _text(d, (W - 32, 84), f"{loc:%Y-%m-%d}", f_small, DIM, anchor="rm")
        d.line([(tl_x0, tl_y), (tl_x1, tl_y)], fill=(*DIM, 150), width=2)
        for j in range(n_sh):
            tx = tl_x(shots[j][0])
            on = j == si
            d.line([(tx, tl_y - (12 if on else 6)), (tx, tl_y + (12 if on else 6))],
                   fill=(*(CYAN if on else DIM), 255), width=4 if on else 2)
        _text(d, (tl_x0, tl_y + 18), f"{shots[0][0] + dt.timedelta(hours=off):%H:%M}", f_small, DIM, anchor="mt")
        _text(d, (tl_x1, tl_y + 18), f"{shots[-1][0] + dt.timedelta(hours=off):%H:%M}", f_small, DIM, anchor="mt")
        if first_t and shots[0][0] <= first_t <= shots[-1][0]:
            fx = tl_x(first_t)
            d.polygon([(fx, tl_y - 10), (fx - 9, tl_y - 26), (fx + 9, tl_y - 26)], fill=(*vcol, 255))
            label = f"أول رصد {first_t + dt.timedelta(hours=off):%H:%M}"
            _text(d, (fx, tl_y - 32), label, f_lab, (*vcol, 255), anchor="mb", rtl=True)

    def add_logo(fr, k):
        if not logo:
            return
        lg, sh = logo
        ph = 2 * math.pi * k / max(n_out, 1)
        cx = LX + PW // 2 + int(PW * 0.26 * math.sin(ph))
        cy = PY + PH // 2 + int(PH * 0.14 * math.sin(2 * ph))
        op = float(C.get("logo_opacity", 0.5))
        fr.alpha_composite(_with_opacity(sh, op), (cx - lg.width // 2 + 2, cy - lg.height // 2 + 3))
        fr.alpha_composite(_with_opacity(lg, op), (cx - lg.width // 2, cy - lg.height // 2))

    out, best, k = [], None, 0
    prev = None
    for i, si in enumerate(seq):
        left, right = panels(si)
        if fire_idx and i == fire_idx and prev is not None:
            # before / after: a line sweeps down once and uncovers the picture with the fire
            pl, pr = prev
            for w in range(wipe_n):
                yy = int(PH * (w + 1) / wipe_n)
                fr = Image.new("RGBA", (W, H), (*BG, 255))
                fr.paste(pl, (LX, PY))
                fr.paste(pr, (RX, PY))
                fr.paste(left.crop((0, 0, PW, yy)), (LX, PY))
                fr.paste(right.crop((0, 0, PW, yy)), (RX, PY))
                fr.alpha_composite(static)
                chrome(fr, si)
                dd = ImageDraw.Draw(fr)
                for x0 in (LX, RX):
                    dd.line([(x0, PY + yy), (x0 + PW, PY + yy)], fill=(*CYAN, 90), width=9)
                    dd.line([(x0, PY + yy), (x0 + PW, PY + yy)], fill=(*WHITE, 255), width=3)
                add_logo(fr, k)
                k += 1
                out.append(fr.convert("RGB"))
        frame = Image.new("RGBA", (W, H), (*BG, 255))
        frame.paste(left, (LX, PY))
        frame.paste(right, (RX, PY))
        frame.alpha_composite(static)
        chrome(frame, si)
        for _ in range(rep):
            fr = frame.copy()
            add_logo(fr, k)
            k += 1
            out.append(fr.convert("RGB"))
        if best is None or centre[si] > best[0]:
            best = (centre[si], len(out) - 1)
        prev = (left, right)
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    snap = Path(f"{base}.jpg")
    out[best[1]].save(snap, quality=90)
    video = _write(out, base, out_fps, str(C.get("format", "mp4")))
    log.info("clip: %d pictures, %d frames", len(shots), len(out))
    return video, snap
