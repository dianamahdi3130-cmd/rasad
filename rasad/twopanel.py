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


def _smooth(x):
    """Ease in and out (slow start, steady middle, gentle stop)."""
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _monitor_overlay(PW, PH):
    """Fine scan lines and a soft dark edge, the look of a surveillance monitor (very light)."""
    a = np.zeros((PH, PW), np.float32)
    a[::4, :] = 18                                        # scan lines
    yy, xx = np.mgrid[0:PH, 0:PW]
    dx, dy = (xx - PW / 2) / (PW / 2), (yy - PH / 2) / (PH / 2)
    a += np.clip((dx * dx + dy * dy - 0.55) * 120, 0, 120)  # vignette
    ov = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    ov.putalpha(Image.fromarray(np.clip(a, 0, 150).astype(np.uint8)))
    return ov


def _dashed_ring(d, cx, cy, r, col, angle, n=24, width=2):
    """A ring of short dashes, turned by `angle` degrees (it turns slowly frame after frame)."""
    for i in range(n):
        a0 = angle + i * 360 / n
        d.arc((cx - r, cy - r, cx + r, cy + r), a0, a0 + 360 / n * 0.55, fill=col, width=width)


def render(cfg, wms, lat, lon, t_end, base, fire_layer, context_layer, frames, info=None):
    """Make the clip. Returns (video, snapshot); raises NotVisible when the fire does not show."""
    from .clip import NotVisible, _write
    from .wms import coverage
    C = cfg["clip"]
    info = info or {}
    W, H = int(C.get("two_w", 1920)), int(C.get("two_h", 1080))
    top_h, foot_h, m, gap = 128, 140, 12, 12
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
    contrast = [c - float(np.nanmedian(v)) for c, (_, v, _) in zip(centre, shots)]
    vis = float(C.get("min_visible", 4))
    if change < vis and max(contrast) < float(C.get("min_contrast", 15)):
        raise NotVisible("the fire does not show in these pictures")
    top = max(hi + float(C.get("min_span", 6)), max(centre))
    lut = _lut(FIRE)
    # the reveal only when the fire really appears during the loop (nothing there in the first picture)
    fire_idx = None
    if contrast[0] < vis * 1.5:
        fire_idx = next((i for i, c in enumerate(centre) if c - centre[0] >= max(vis, 0.4 * change)), None)
    shown_from = fire_idx if fire_idx is not None else 0

    stack = np.stack([v for _, v, _ in shots])
    hot = stack.max(axis=0) - float(np.median(stack[0]))
    R = int(round(float(C.get("snap_km", 6)) / px_km))
    yy, xx = np.mgrid[0:ny, 0:nx]
    near = (yy - ny / 2) ** 2 + (xx - nx / 2) ** 2 <= R * R
    hv = np.where(near, hot, -1e9)
    hmax = float(hv.max())
    if hmax >= vis:
        wts = np.where(hv >= 0.7 * hmax, hv, 0.0)
        hr, hc = (yy * wts).sum() / wts.sum(), (xx * wts).sum() / wts.sum()
        tx_, ty_ = (hc + 0.5) / nx * PW, (hr + 0.5) / ny * PH
    else:
        tx_, ty_ = PW / 2, PH / 2

    off = float(cfg["local_time"]["offset_hours"])
    tzname = cfg["local_time"].get("name", "")
    first = info.get("first")
    first_t = dt.datetime.fromisoformat(first.replace("Z", "+00:00")) if first else None
    if first_t is None and fire_idx is not None:
        first_t = shots[fire_idx][0]
    verdict = info.get("verdict") or ""
    vcol = VERDICT_COL.get(verdict, CYAN)
    f_sub = _font(27, "ar")
    f_brand, f_clock = _font(30, "mono"), _font(56, "mono")
    f_coord, f_small = _font(30, "mono"), _font(18, "monol")
    f_lab, f_first, f_badge = _font(26, "ar"), _font(30, "arb"), _font(32, "arb")

    static = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(static)
    d.rectangle((0, 0, W, top_h), fill=(*BG, 255))
    d.line([(0, top_h - 1), (W, top_h - 1)], fill=(*CYAN, 70), width=2)
    _text(d, (64, top_h // 2), str(C.get("hud_title", "Alfaqaar313")), f_brand, CYAN, anchor="lm")
    title = info.get("where") or f"{lat:.4f}, {lon:.4f}"
    max_w = W - 2 * 450
    if _width(d, title, _font(38, "arb"), rtl=True) > max_w and "(" in title:
        title = title[:title.rfind("(")].rstrip()
    for size in (46, 42, 38, 34, 31, 28):
        f_title = _font(size, "arb")
        if _width(d, title, f_title, rtl=True) <= max_w:
            break
    else:
        while len(title) > 10 and _width(d, title + "…", f_title, rtl=True) > max_w:
            title = title[:-1]
        title = title.rstrip("، ") + "…"
    _text(d, (W // 2, 48), title, f_title, WHITE, anchor="mm", rtl=True)
    region, country = info.get("region") or "", info.get("country") or ""
    if region and country and (region in country or country in region):
        region = ""
    sub = " • ".join(x for x in (region, country) if x and x not in title)
    if sub:
        _text(d, (W // 2, 100), sub, f_sub, DIM, anchor="mm", rtl=True)
    overlay = _monitor_overlay(PW, PH)
    for x0, label in ((LX, "حراري"), (RX, "مرئي")):
        static.alpha_composite(overlay, (x0, PY))
        _brackets(d, x0 + 8, PY + 8, x0 + PW - 8, PY + PH - 8, (*CYAN, 230), arm=44)
        lw = _width(d, label, f_lab, rtl=True)
        d.rounded_rectangle((x0 + 26, PY + 26, x0 + 26 + lw + 32, PY + 70), radius=8, fill=(0, 0, 0, 160))
        _text(d, (x0 + 42 + lw // 2, PY + 48), label, f_lab, WHITE, anchor="mm", rtl=True)
    fy = H - foot_h
    d.rectangle((0, fy, W, H), fill=(*BG, 255))
    d.line([(0, fy), (W, fy)], fill=(*CYAN, 70), width=2)
    _text(d, (40, fy + 58), f"{lat:.4f}°N  {lon:.4f}°E", f_coord, WHITE, anchor="lm")
    _text(d, (40, fy + 104), "© EUMETSAT", f_small, (92, 104, 120), anchor="lm")
    if verdict:
        vw = _width(d, verdict, f_badge, rtl=True) + 56
        d.rounded_rectangle((W - 40 - vw, fy + 40, W - 40, fy + 100), radius=12, fill=(*vcol, 245))
        _text(d, (W - 40 - vw // 2, fy + 70), verdict, f_badge, (12, 12, 14), anchor="mm", rtl=True)

    # a few small, faint logos drifting slowly over the pictures (hard to paint out, easy to ignore)
    marks = []
    lp = cfg.get("watermark_logo")
    if lp:
        p = Path(lp)
        lg = _logo(p if p.is_absolute() else ROOT / p, int(C.get("mark_width", 150)))
        if lg:
            small = _with_opacity(lg[0], float(C.get("mark_opacity", 0.22)))
            n_marks = int(C.get("marks", 4))
            for j in range(n_marks):
                x0 = LX if j % 2 == 0 else RX
                marks.append((small, x0, (j * 0.37 + 0.13) % 1.0, (j * 0.61 + 0.27) % 1.0, 1 if j % 2 else -1))

    pmask = Image.new("L", (W, H), 0)  # rings are drawn only inside the two pictures
    pmd = ImageDraw.Draw(pmask)
    for x0 in (LX, RX):
        pmd.rectangle((x0, PY, x0 + PW - 1, PY + PH - 1), fill=255)
    pmask_np = np.asarray(pmask, np.float32) / 255

    def clipped(layer):
        a = np.asarray(layer.getchannel("A"), np.float32) * pmask_np
        layer.putalpha(Image.fromarray(a.astype(np.uint8)))
        return layer

    sat_fps = int(C.get("fps", 2))
    out_fps = int(C.get("out_fps", 24))
    rep = max(1, out_fps // sat_fps)
    reveal_n = int(out_fps * float(C.get("reveal_s", 1.6)))
    seq = list(range(len(shots))) + [len(shots) - 1] * int(C.get("hold_last", 4))
    n_out = len(seq) * rep + (reveal_n if fire_idx else 0)
    r = int(C.get("ring_px", 50))
    tl_x0, tl_x1, tl_y = 720, W - 720, fy + 84
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

    def chrome(frame, si, k, fire_on):
        """Target marks, clock, live dot and timeline for picture si, output frame k."""
        t = shots[si][0]
        d = ImageDraw.Draw(frame)
        rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rings)
        for x0 in (LX, RX):
            cx, cy = x0 + int(tx_), PY + int(ty_)
            _reticle(rd, cx, cy, r, CYAN)
            _dashed_ring(rd, cx, cy, r + 26, (*CYAN, 150), angle=(k * 1.5) % 360)
            if fire_on:  # a radar ping from the fire, about every 1.6 s
                ph = (k % int(out_fps * 1.6)) / (out_fps * 1.6)
                rr = int(r + 10 + ph * 120)
                rd.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(*vcol, int(200 * (1 - ph))), width=3)
        frame.alpha_composite(clipped(rings))
        loc = t + dt.timedelta(hours=off)
        _text(d, (W - 40, top_h // 2 + 2), f"{loc:%H:%M}", f_clock, WHITE, anchor="rm")
        cw = _width(d, f"{loc:%H:%M}", f_clock)
        _text(d, (W - 40 - cw - 16, top_h // 2 + 6), tzname, f_sub, DIM, anchor="rm", rtl=True)
        live = 0.5 + 0.5 * math.sin(2 * math.pi * k / out_fps)  # the 'watching' dot breathes
        d.ellipse((34, top_h // 2 - 8, 50, top_h // 2 + 8), fill=(235, 64, 64, int(120 + 135 * live)))
        px = tl_x(t)
        d.rounded_rectangle((tl_x0, tl_y - 4, tl_x1, tl_y + 4), radius=4, fill=(*DIM, 100))
        d.rounded_rectangle((tl_x0, tl_y - 4, max(tl_x0 + 8, px), tl_y + 4), radius=4, fill=(*CYAN, 255))
        d.ellipse((px - 11, tl_y - 11, px + 11, tl_y + 11), fill=(*CYAN, 255), outline=(*BG, 255), width=3)
        if first_t and shots[0][0] <= first_t <= shots[-1][0]:
            fx = tl_x(first_t)
            d.line([(fx, tl_y - 20), (fx, tl_y + 14)], fill=(*vcol, 255), width=4)
            _text(d, (fx, tl_y - 26), f"أول رصد {first_t + dt.timedelta(hours=off):%H:%M}", f_first, (*vcol, 255),
                  anchor="mb", rtl=True)

    def add_marks(fr, k):
        for (img, x0, fx, fy_, sgn) in marks:
            ph = k / max(n_out, 1)
            x = x0 + int(((fx + 0.18 * ph * sgn) % 1.0) * (PW - img.width))
            y = PY + int(((fy_ + 0.12 * math.sin(2 * math.pi * ph)) % 1.0) * (PH - img.height))
            fr.alpha_composite(img, (x, y))

    out, best, k, prev = [], None, 0, None
    for i, si in enumerate(seq):
        left, right = panels(si)
        fire_on = si >= shown_from
        if fire_idx and i == fire_idx and prev is not None:
            # the fire appears: a circle opens from the target and uncovers the new picture
            pl, pr = prev
            rmax = int(math.hypot(PW, PH))
            for w in range(reveal_n):
                rr = int(rmax * _smooth((w + 1) / reveal_n))
                fr = Image.new("RGBA", (W, H), (*BG, 255))
                mask = Image.new("L", (PW, PH), 0)
                ImageDraw.Draw(mask).ellipse((int(tx_) - rr, int(ty_) - rr, int(tx_) + rr, int(ty_) + rr), fill=255)
                for (old, new_, x0) in ((pl, left, LX), (pr, right, RX)):
                    fr.paste(old, (x0, PY))
                    fr.paste(new_, (x0, PY), mask)
                fr.alpha_composite(static)
                chrome(fr, si, k, True)
                edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ed = ImageDraw.Draw(edge)
                fade = int(255 * (1 - _smooth((w + 1) / reveal_n)) ** 0.5)
                for x0 in (LX, RX):  # the edge of the opening circle, with a soft glow
                    cx, cy = x0 + int(tx_), PY + int(ty_)
                    ed.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(*CYAN, max(30, fade // 3)), width=12)
                    ed.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(*WHITE, max(40, fade)), width=3)
                fr.alpha_composite(clipped(edge))
                add_marks(fr, k)
                k += 1
                out.append(fr.crop((0, 0, W, H)).convert("RGB"))
        frame = Image.new("RGBA", (W, H), (*BG, 255))
        frame.paste(left, (LX, PY))
        frame.paste(right, (RX, PY))
        frame.alpha_composite(static)
        for _ in range(rep):
            fr = frame.copy()
            chrome(fr, si, k, fire_on)
            add_marks(fr, k)
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
