"""Clip in a clean monitoring-display style: the heat picture (left) and the natural picture
(right) of the same minutes side by side, the brand and the place above, the coordinates and a
timeline with the time of the first sighting below, and the word "مؤكد" only when the fire is
confirmed.

The circle stays on the fire's position. It moves only onto a small hot speck that really shows
in the pictures next to that position (a speck that appears with the fire, or at night one that
stands out from the dark sea); bright land or cloud a few km away never pulls it. A short line
from the circle carries the fire's strength in megawatts at each moment: measured values as they
are, values between measurements estimated from the heat picture (marked ≈).

When the fire first appears during the loop, a circle opens from it and uncovers the new picture
(before / after). A faint, slanted pattern of the channel logo drifts slowly over both pictures,
hard to paint out and easy to ignore."""
from __future__ import annotations

import datetime as dt
import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from .util import log, sun_elevation

NEAREST = getattr(Image, "Resampling", Image).NEAREST
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
BICUBIC = getattr(Image, "Resampling", Image).BICUBIC
ROOT = Path(__file__).resolve().parent.parent

# black -> deep red -> orange -> yellow-white: the usual look of the fire picture at night
FIRE = [(0.00, (3, 2, 6)), (0.35, (18, 4, 8)), (0.50, (42, 6, 10)), (0.62, (150, 22, 6)),
        (0.74, (236, 92, 0)), (0.86, (255, 178, 34)), (1.00, (255, 252, 218))]
BG = (6, 9, 14)
CYAN = (64, 214, 236)
WHITE = (240, 244, 250)
DIM = (128, 142, 160)
SURE = (232, 62, 62)    # "مؤكد"
HOT = (255, 150, 50)    # the first sighting of a fire that is not confirmed (yet)
SIGMA_OVER_A = 5.670374e-8 / 3.0e-9  # fire radiative power from 3.8 micron radiance (Wooster et al.)

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
    elif kind in ("brand", "brandm"):  # the channel's name in Latin letters
        name = "ChakraPetch-Bold.ttf" if kind == "brand" else "ChakraPetch-Medium.ttf"
        cands = [str(ROOT / "assets/fonts" / name), "DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
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


def _spaced(d, x, y, text, font, fill, track):
    """Latin capitals with extra letter spacing, from x on the baseline y. Returns where it ends."""
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + track
    return x - track


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


# ----------------------------------------------------------------- the brand mark
def _bez(p0, p1, p2, p3, n=48):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


# a flame with a small side tongue, and its bright core (unit box, y downwards)
_FLAME = [((0.50, 1.00), (0.78, 1.00), (0.95, 0.82), (0.95, 0.60)),
          ((0.95, 0.60), (0.95, 0.38), (0.80, 0.22), (0.62, 0.00)),
          ((0.62, 0.00), (0.64, 0.20), (0.52, 0.32), (0.40, 0.42)),
          ((0.40, 0.42), (0.34, 0.36), (0.28, 0.31), (0.24, 0.22)),
          ((0.24, 0.22), (0.10, 0.40), (0.05, 0.56), (0.05, 0.66)),
          ((0.05, 0.66), (0.05, 0.86), (0.24, 1.00), (0.50, 1.00))]
_CORE = [((0.50, 0.95), (0.66, 0.95), (0.75, 0.85), (0.75, 0.74)),
         ((0.75, 0.74), (0.75, 0.62), (0.63, 0.55), (0.57, 0.44)),
         ((0.57, 0.44), (0.53, 0.55), (0.40, 0.60), (0.32, 0.70)),
         ((0.32, 0.70), (0.25, 0.80), (0.32, 0.95), (0.50, 0.95))]


def _shape(segs, x0, y0, w, h):
    pts = []
    for s in segs:
        pts += _bez(*s)
    return [(x0 + x * w, y0 + y * h) for x, y in pts]


def _vgrad(size, top, h, stops):
    """A vertical colour ramp over rows top..top+h of an image of the given size."""
    W, H = size
    ys = np.clip((np.arange(H) - top) / max(h, 1), 0, 1)
    p = [s for s, _ in stops]
    col = np.stack([np.interp(ys, p, [c[i] for _, c in stops]) for i in range(3)], axis=1)
    return Image.fromarray(np.repeat(col[:, None, :], W, axis=1).astype(np.uint8))


def _emblem(S, ss=4):
    """The brand mark: a target ring with four ticks around a flame (drawn large, then reduced)."""
    s = S * ss
    c = s / 2
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw, r = max(2, round(s * 0.05)), s * 0.40
    for a0 in (0, 90, 180, 270):
        d.arc((c - r, c - r, c + r, c + r), a0 + 13, a0 + 77, fill=(*CYAN, 255), width=lw)
    for a in (0, 90, 180, 270):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line([(c + ca * (r - s * 0.07), c + sa * (r - s * 0.07)),
                (c + ca * (r + s * 0.09), c + sa * (r + s * 0.09))], fill=(*CYAN, 255), width=lw)
    fw, fh = s * 0.38, s * 0.48
    fx, fy = c - fw / 2, c - fh / 2 - s * 0.01
    for segs, stops in ((_FLAME, [(0, (255, 120, 40)), (0.55, (250, 84, 28)), (1, (226, 48, 30))]),
                        (_CORE, [(0, (255, 236, 140)), (0.6, (255, 214, 90)), (1, (255, 178, 50))])):
        m = Image.new("L", (s, s), 0)
        ImageDraw.Draw(m).polygon(_shape(segs, fx, fy, fw, fh), fill=255)
        im.alpha_composite(Image.merge("RGBA", (*_vgrad((s, s), fy, fh, stops).split(), m)))
    return im.resize((S, S), LANCZOS)


def _glow(S):
    """A soft warm light behind the flame; it breathes while the clip plays."""
    s = S * 2
    g = Image.new("L", (s, s), 0)
    ImageDraw.Draw(g).ellipse((s * 0.34, s * 0.32, s * 0.66, s * 0.68), fill=255)
    out = Image.new("RGBA", (s, s), (255, 110, 30, 0))
    out.putalpha(g.filter(ImageFilter.GaussianBlur(S * 0.14)))
    return out


# ----------------------------------------------------------------- the logo pattern
def _tile_canvas(path, W, H, width, op, gap_x, gap_y, angle):
    """The channel logo repeated in staggered rows over twice the frame, turned on a slant.
    Returns the picture and the repeat length along a row (the pattern drifts along its rows)."""
    try:
        lg = Image.open(path).convert("RGBA")
    except OSError:
        return None, 0
    small = _with_opacity(lg.resize((width, max(1, int(lg.height * width / lg.width))), LANCZOS), op)
    big = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    sx, sy = small.width + gap_x, small.height + gap_y
    for j, y in enumerate(range(0, H * 2, sy)):
        for x in range((j % 2) * sx // 2, W * 2, sx):
            big.alpha_composite(small, (x, y))
    return big.rotate(-angle, resample=BICUBIC, expand=False), sx


# ----------------------------------------------------------------- reading the heat picture
def _blob(mask, r0, c0, limit=4000):
    """The group of touching pixels of `mask` that contains (r0, c0) (stops growing at `limit`)."""
    out = np.zeros(mask.shape, bool)
    if not mask[r0, c0]:
        return out
    H, W = mask.shape
    q, n = deque([(r0, c0)]), 1
    out[r0, c0] = True
    while q and n < limit:
        r, c = q.popleft()
        for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= rr < H and 0 <= cc < W and mask[rr, cc] and not out[rr, cc]:
                out[rr, cc] = True
                n += 1
                q.append((rr, cc))
    return out


def _radiance(v, bt):
    """Picture value (0-255) -> 3.8 micron radiance (W m-2 sr-1 um-1), for a picture drawn with a
    known stretch [lowest K, highest K, gamma]: the Fire Temperature RGB's red is 0..60 C, gamma 0.4."""
    lo, hi, gamma = bt
    T = lo + (hi - lo) * np.power(np.clip(v, 0, 255) / 255.0, gamma)
    lam = 3.8
    return 1.191042e8 / (lam ** 5 * (np.exp(1.4387752e4 / (lam * T)) - 1.0))


def _mw_text(v):
    return (f"{v:.1f}" if v < 10 else f"{v:.0f}") + " ميغاواط"


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
    ch = fire_layer.get("channel")  # e.g. only the red (3.8 micron) part of the Fire Temperature RGB
    hot_bright = fire_layer.get("hot_is_bright", True)
    if hot_bright in ("auto", None):  # plain infrared pictures draw warm as dark
        hot_bright = bool(fire_layer.get("hot_is_bright_fallback", False))

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
        a = np.asarray(img).astype(np.float32)
        g = a[..., int(ch)] if ch is not None else a[..., :3].mean(axis=2)
        if not hot_bright:
            g = 255.0 - g
        if shots and np.array_equal(g, shots[-1][1]):
            continue
        shots.append((actual or t, g, ctx))
    if len(shots) < 2:
        raise RuntimeError(f"only {len(shots)} usable picture(s) from {fire_layer['name']}")

    # ---- where does the fire show? (a small speck right next to the reported position)
    first = info.get("first")
    first_t = dt.datetime.fromisoformat(first.replace("Z", "+00:00")) if first else None
    stack = np.stack([v for _, v, _ in shots])
    yy, xx = np.mgrid[0:ny, 0:nx]
    dist = np.hypot(yy + 0.5 - ny / 2, xx + 0.5 - nx / 2) * px_km  # km from the reported position
    night = sun_elevation(lat, lon, first_t or shots[-1][0]) < -6
    vis = float(C.get("min_visible", 4))
    max_px = float(C.get("speck_max_km2", 40)) / px_km ** 2
    before = [i for i, (t, _, _) in enumerate(shots)
              if first_t and t < first_t - dt.timedelta(minutes=cad / 2)]
    after = [i for i in range(len(shots)) if i not in before]
    speck = ref = None
    if before and after:  # it appeared during the loop: what rose where it is reported?
        ref = np.median(stack[before], axis=0)
        rise = stack[after].max(axis=0) - ref
        reach = float(C.get("snap_km", 6)) if night else float(C.get("snap_km_day", 4))
        cand = np.where(dist <= reach, rise, -1e9)
        r0, c0 = np.unravel_index(int(np.argmax(cand)), cand.shape)
        if cand[r0, c0] >= (vis if night else 2 * vis):
            blob = _blob(rise >= 0.5 * cand[r0, c0], r0, c0)
            if blob.sum() <= max_px:  # a fire is a speck; cloud and warming land are wide
                speck = blob
    if speck is None:  # burning in every picture: a spot clearly hotter than its surroundings
        top_img = stack.max(axis=0)
        bg = float(np.median(top_img[(dist >= 5) & (dist <= 12)]))
        cand = np.where(dist <= (3.0 if night else 2.0), top_img, -1e9)
        r0, c0 = np.unravel_index(int(np.argmax(cand)), cand.shape)
        if cand[r0, c0] - bg >= float(C.get("static_min", 25)) * (1 if night else 1.4):
            blob = _blob(top_img >= bg + 0.5 * (cand[r0, c0] - bg), r0, c0)
            if blob.sum() <= max_px:
                speck, ref = blob, np.full_like(top_img, bg)
                before = []
    if speck is None:
        raise NotVisible("the fire does not show in these pictures")
    excess = np.maximum(stack - ref, 0)
    wts = np.where(speck, excess.max(axis=0) + 1e-3, 0.0)
    hr, hc = (yy * wts).sum() / wts.sum(), (xx * wts).sum() / wts.sum()
    tx_, ty_ = (hc + 0.5) / nx * PW, (hr + 0.5) / ny * PH
    strength = [float(e[speck].max()) for e in excess]
    peak = max(strength)
    fire_idx = None
    if before:  # the reveal only when the fire really appears during the loop
        fire_idx = next((i for i in after if strength[i] >= max(vis, 0.5 * peak)), None)
    shown_from = fire_idx if fire_idx is not None else 0

    # ---- the fire's strength at each moment: measurements, and estimates between them
    meas = []
    for row in info.get("frp") or []:
        try:
            meas.append((dt.datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")), float(row[1])))
        except (ValueError, TypeError, IndexError):
            continue
    slot = dt.timedelta(minutes=cad / 2 + 1)
    measured = [max([mw for tm, mw in meas if abs(tm - t) <= slot], default=None) for t, _, _ in shots]
    est = [None] * len(shots)
    if fire_layer.get("bt"):
        area = (px_km * 1000.0) ** 2
        rad_ref = _radiance(ref, fire_layer["bt"])
        energy = [float(np.maximum(_radiance(v, fire_layer["bt"]) - rad_ref, 0)[speck].sum()) * area
                  * SIGMA_OVER_A / 1e6 for v in stack]
        seen = [i for i in range(len(shots)) if i >= shown_from and strength[i] >= vis and energy[i] > 1e-6]
        known = [measured[i] for i in range(len(shots)) if measured[i] is not None]
        if seen and known:
            # scaled so that the fire's usual brightness in these pictures matches the satellites'
            # own measurement; small fires flicker from picture to picture, so each value is
            # smoothed with its neighbours
            k = float(np.median(known)) / float(np.median([energy[i] for i in seen]))
            raw = {i: k * energy[i] for i in seen}
            for i in seen:
                near = [raw[j] for j in (i - 1, i, i + 1) if j in raw]
                est[i] = float(np.mean(near))
    shown_mw, last = [], None
    for i in range(len(shots)):
        if measured[i] is not None and i >= shown_from:
            last = (measured[i], False)
        elif est[i] is not None and est[i] >= 0.5:
            last = (est[i], True)
        elif est[i] is not None:
            last = None
        shown_mw.append(last if i >= shown_from else None)

    base_img = stack[0]
    lo = float(np.percentile(base_img, 1))
    hi = max(float(np.percentile(base_img, 99.5)), lo + 4)
    top = max(hi + float(C.get("min_span", 6)), float(stack[:, speck].max()))
    lut = _lut(FIRE)

    off = float(cfg["local_time"]["offset_hours"])
    tzname = cfg["local_time"].get("name", "")
    if first_t is None and fire_idx is not None:
        first_t = shots[fire_idx][0]
    verdict = info.get("verdict") or ""
    sure = verdict == "مؤكد"
    vcol = SURE if sure else HOT
    f_sub = _font(27, "ar")
    f_clock = _font(56, "mono")
    f_coord, f_small = _font(30, "mono"), _font(18, "monol")
    f_lab, f_first, f_badge, f_mw = _font(26, "ar"), _font(30, "arb"), _font(32, "arb"), _font(30, "arb")

    static = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(static)
    d.rectangle((0, 0, W, top_h), fill=(*BG, 255))
    d.line([(0, top_h - 1), (W, top_h - 1)], fill=(*CYAN, 70), width=2)
    # the brand: mark, then the name in two tones ("ALFAQAAR | FIRMS")
    em_s = 64
    emblem, glow = _emblem(em_s), _glow(em_s)
    em_x, em_y = 34, (top_h - em_s) // 2
    words = str(C.get("hud_title", "ALFAQAAR FIRMS")).strip().upper().split()
    f_b1, f_b2 = _font(34, "brand"), _font(34, "brandm")
    base_y = top_h // 2 + 12
    x = _spaced(d, em_x + em_s + 20, base_y, words[0] if words else "", f_b1, WHITE, 3)
    if len(words) > 1:
        d.line([(x + 14, top_h // 2 - 15), (x + 14, top_h // 2 + 15)], fill=(*CYAN, 170), width=2)
        x = _spaced(d, x + 28, base_y, " ".join(words[1:]), f_b2, CYAN, 3)
    brand_end = x
    clock_w = _width(d, "00:00", f_clock)
    right_start = W - 40 - clock_w - 16 - _width(d, tzname, f_sub, rtl=True)
    max_w = int(2 * min(W / 2 - brand_end - 40, right_start - W / 2 - 40))
    title = info.get("where") or f"{lat:.4f}, {lon:.4f}"
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
    if sure:  # only a confirmed fire carries a verdict on the clip
        vw = _width(d, verdict, f_badge, rtl=True) + 56
        d.rounded_rectangle((W - 40 - vw, fy + 40, W - 40, fy + 100), radius=12, fill=(*SURE, 245))
        _text(d, (W - 40 - vw // 2, fy + 70), verdict, f_badge, (12, 12, 14), anchor="mm", rtl=True)

    # the faint logo pattern over the two pictures, drifting slowly along its rows
    tiles, period = None, 0
    lp = cfg.get("watermark_logo")
    if lp and float(C.get("logo_tile_opacity", 0.05)) > 0:
        p = Path(lp)
        gx, gy = C.get("logo_tile_gap", [130, 120])
        tiles, period = _tile_canvas(p if p.is_absolute() else ROOT / p, W, H, int(C.get("logo_tile_width", 150)),
                                     float(C.get("logo_tile_opacity", 0.05)), int(gx), int(gy),
                                     float(C.get("logo_tile_angle", 22)))
    t_cos, t_sin = math.cos(math.radians(float(C.get("logo_tile_angle", 22)))), \
        math.sin(math.radians(float(C.get("logo_tile_angle", 22))))
    t_speed = float(C.get("logo_tile_speed", 2.0))

    def add_tiles(fr, k):
        if tiles is None:
            return
        s = (k * t_speed) % period - period / 2
        ox, oy = int(round(W / 2 + s * t_cos)), int(round(H / 2 + s * t_sin))
        for x0 in (LX, RX):
            fr.alpha_composite(tiles.crop((ox + x0, oy + PY, ox + x0 + PW, oy + PY + PH)), (x0, PY))

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
    r = int(C.get("ring_px", 50))
    tl_x0, tl_x1, tl_y = 720, W - 720, fy + 84
    span = (shots[-1][0] - shots[0][0]).total_seconds() or 1
    callout = {"since": None}

    def tl_x(t):
        return tl_x0 + (tl_x1 - tl_x0) * min(1.0, max(0.0, (t - shots[0][0]).total_seconds() / span))

    def panels(si):
        t, v, ctx = shots[si]
        x = np.where(v <= hi, 0.5 * (v - lo) / (hi - lo), 0.5 + 0.5 * (v - hi) / (top - hi))
        left = Image.fromarray(lut[np.clip(np.round(x * 255), 0, 255).astype(np.uint8)]).resize((PW, PH), NEAREST)
        right = (Image.fromarray(ctx).resize((PW, PH), NEAREST) if ctx is not None
                 else Image.new("RGB", (PW, PH), (12, 16, 28)))
        return left, right

    def draw_callout(d, si, k):
        """A short line from the circle to the fire's strength at this moment (heat picture only)."""
        val = shown_mw[si]
        if val is None:
            callout["since"] = None
            return
        if callout["since"] is None:
            callout["since"] = k
        grow = _smooth((k - callout["since"] + 1) / (out_fps * 0.45))  # the line draws itself in
        cx, cy = LX + int(tx_), PY + int(ty_)
        sx = -1 if tx_ > PW * 0.62 else 1   # point towards the open side of the picture
        sy = 1 if ty_ < PH * 0.30 else -1
        text = ("≈ " if val[1] else "") + _mw_text(val[0])
        tw = _width(d, text, f_mw, rtl=True)
        rr = r + 12
        p0 = (cx + sx * rr * 0.7071, cy + sy * rr * 0.7071)
        p1 = (p0[0] + sx * 46, p0[1] + sy * 46)
        p2 = (p1[0] + sx * (tw + 30), p1[1])
        seg1 = min(1.0, grow * 2)
        seg2 = max(0.0, grow * 2 - 1)
        q1 = (p0[0] + (p1[0] - p0[0]) * seg1, p0[1] + (p1[1] - p0[1]) * seg1)
        d.line([p0, q1], fill=(0, 0, 0, 150), width=6)
        d.line([p0, q1], fill=(*CYAN, 255), width=3)
        if seg2 > 0:
            q2 = (p1[0] + (p2[0] - p1[0]) * seg2, p1[1])
            d.line([p1, q2], fill=(0, 0, 0, 150), width=6)
            d.line([p1, q2], fill=(*CYAN, 255), width=3)
            d.ellipse((q2[0] - 5, q2[1] - 5, q2[0] + 5, q2[1] + 5), fill=(*CYAN, 255))
        if grow >= 0.999:
            mid = (p1[0] + p2[0]) / 2
            ty = p1[1] - 24 if sy < 0 else p1[1] + 26
            _text(d, (mid + 2, ty + 2), text, f_mw, (0, 0, 0, 200), anchor="mm", rtl=True)
            _text(d, (mid, ty), text, f_mw, WHITE, anchor="mm", rtl=True)

    def chrome(frame, si, k, fire_on):
        """Target marks, strength line, brand light, clock and timeline for picture si, frame k."""
        t = shots[si][0]
        live = 0.5 + 0.5 * math.sin(2 * math.pi * k / (out_fps * 1.6))  # the brand light breathes
        gl = glow.copy()
        gl.putalpha(gl.getchannel("A").point(lambda v: int(v * (0.18 + 0.42 * live))))
        frame.alpha_composite(gl, (em_x + em_s // 2 - gl.width // 2, em_y + em_s // 2 - gl.height // 2))
        frame.alpha_composite(emblem, (em_x, em_y))
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
        draw_callout(rd, si, k)
        frame.alpha_composite(clipped(rings))
        d = ImageDraw.Draw(frame)
        loc = t + dt.timedelta(hours=off)
        _text(d, (W - 40, top_h // 2 + 2), f"{loc:%H:%M}", f_clock, WHITE, anchor="rm")
        cw = _width(d, f"{loc:%H:%M}", f_clock)
        _text(d, (W - 40 - cw - 16, top_h // 2 + 6), tzname, f_sub, DIM, anchor="rm", rtl=True)
        px = tl_x(t)
        d.rounded_rectangle((tl_x0, tl_y - 4, tl_x1, tl_y + 4), radius=4, fill=(*DIM, 100))
        d.rounded_rectangle((tl_x0, tl_y - 4, max(tl_x0 + 8, px), tl_y + 4), radius=4, fill=(*CYAN, 255))
        d.ellipse((px - 11, tl_y - 11, px + 11, tl_y + 11), fill=(*CYAN, 255), outline=(*BG, 255), width=3)
        if first_t and shots[0][0] <= first_t <= shots[-1][0]:
            fx = tl_x(first_t)
            d.line([(fx, tl_y - 20), (fx, tl_y + 14)], fill=(*vcol, 255), width=4)
            _text(d, (fx, tl_y - 26), f"أول رصد {first_t + dt.timedelta(hours=off):%H:%M}", f_first, (*vcol, 255),
                  anchor="mb", rtl=True)

    out, k, prev = [], 0, None
    best = (max(range(len(shots)), key=lambda i: strength[i]) + 1) * rep - 1
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
                add_tiles(fr, k)
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
                k += 1
                out.append(fr.convert("RGB"))
            if i <= best // rep:
                best += reveal_n
        frame = Image.new("RGBA", (W, H), (*BG, 255))
        frame.paste(left, (LX, PY))
        frame.paste(right, (RX, PY))
        for _ in range(rep):
            fr = frame.copy()
            add_tiles(fr, k)
            fr.alpha_composite(static)
            chrome(fr, si, k, fire_on)
            k += 1
            out.append(fr.convert("RGB"))
        prev = (left, right)
    Path(base).parent.mkdir(parents=True, exist_ok=True)
    snap = Path(f"{base}.jpg")
    out[min(best, len(out) - 1)].save(snap, quality=90)
    video = _write(out, base, out_fps, str(C.get("format", "mp4")))
    log.info("clip: %d pictures, %d frames, speck %.1f km from the reported point, strength %s", len(shots),
             len(out), math.hypot(tx_ - PW / 2, ty_ - PH / 2) * 2 * half_h / PH,
             [None if v is None else round(v[0], 1) for v in shown_mw])
    return video, snap
