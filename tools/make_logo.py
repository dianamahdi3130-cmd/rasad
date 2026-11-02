"""Turn a white logo shown on a plain grey card into a clean transparent PNG for the clips.

White letters are kept as solid white shapes (small gaps from the brush texture are closed),
the coloured icon keeps its colours, and the grey card, its vignette and the soft halo go away.
usage: python tools/make_logo.py input.png assets/logo.png
"""
import sys

import numpy as np
from PIL import Image, ImageFilter


def _shift_max(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def _shift_min(m, r):
    return ~_shift_max(~m, r)


def main(src, dst):
    im = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    L = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
    mx, mn = im.max(axis=2), im.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    core = (L >= 232) & (sat < 0.15)
    core = _shift_min(_shift_max(core, 3), 3)          # close the small gaps of the brush texture
    white = np.asarray(Image.fromarray((core * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.2))).astype(np.float32) / 255
    blue = np.clip((sat - 0.22) / 0.30, 0, 1) * (im[..., 2] > im[..., 0])
    alpha = np.maximum(white, blue)
    rgb = np.where((blue > white)[..., None], im, 255.0)
    out = np.dstack([rgb, alpha * 255]).astype(np.uint8)
    ys, xs = np.nonzero(alpha > 0.1)
    pad = 16
    crop = out[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]
    Image.fromarray(crop, "RGBA").save(dst, optimize=True)
    print(f"{dst}: {crop.shape[1]}x{crop.shape[0]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
