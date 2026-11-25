"""A previous picture with a transparent (no data) stripe over a steadily hot sea pixel (e.g. a
platform flare, which the rule would reject later anyway)."""
import copy, datetime as dt
from unittest.mock import Mock, patch
import numpy as np
from rasad import engine, seawatch
from rasad.util import box_around

UTC = dt.timezone.utc
cfg = copy.deepcopy(engine.DEFAULTS)
cfg["seawatch"]["steady_min_above"] = 8
box = {"id": "t", "bbox": box_around(25.0, 53.0, 25)}
W, H = seawatch.box_size(box, 0.25)
r, c = H // 2, W // 2
WHEN = dt.datetime(2026, 11, 25, 18, 0, tzinfo=UTC)

def get(layer, bbox, w, h, t, channel=None):
    a = np.full((H, W), 30.0, np.float32)
    if channel in (1, 2):
        return np.zeros((H, W), np.float32)
    a[r - 1:r + 2, c - 1:c + 2] += 60           # a flare, hot in every picture
    if t == WHEN - dt.timedelta(minutes=10):   # the picture before: a stripe not drawn (alpha 0 -> NaN in _gray)
        a[r - 20:r + 20, :] = np.nan
    return a
pics = Mock(); pics.get.side_effect = get
open_sea = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
with patch.dict(seawatch._SEA, {}, clear=True), \
        patch.object(seawatch, "land_mask_points", return_value=open_sea), \
        patch.object(seawatch, "sun_elevation", return_value=-30.0), \
        patch.object(seawatch, "_ir105_change", return_value=0.7):
    try:
        print(seawatch.find_spots(cfg, pics, box, WHEN, mem=None, geo=None))
    except Exception as e:
        print("RAISED:", type(e).__name__, e)

# does _gray really make NaN for a partly transparent picture kept in memory?
raw = np.zeros((40, 40, 4), np.uint8); raw[..., 3] = 255; raw[:10, :, 3] = 0
g = seawatch._gray(raw, 0)
print("partly transparent picture -> NaN rows:", int(np.isnan(g).any(axis=1).sum()), "of 40 (mean alpha", raw[..., 3].mean(), ")")
