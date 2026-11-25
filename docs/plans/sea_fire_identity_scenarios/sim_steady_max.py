"""A sea box with three platform flares (as every night in the southern Gulf: Zakum and others read
40-150) and one steady fire in open water, 20 above the sea, far from all of them."""
import copy, datetime as dt
from unittest.mock import Mock, patch
import numpy as np
from rasad import engine, seawatch, patterns
from rasad.util import box_around, px_to_latlon, haversine_km

UTC = dt.timezone.utc
WHEN = dt.datetime(2026, 11, 25, 18, 0, tzinfo=UTC)
cfg = copy.deepcopy(engine.DEFAULTS)
cfg["seawatch"]["steady_min_above"] = 8
box = {"id": "gulf_south", "bbox": box_around(25.3, 53.0, 60)}
W, H = seawatch.box_size(box, 0.25)

def run(n_flares):
    flares = [(H // 4, W // 4), (H // 4, 3 * W // 4), (3 * H // 4, W // 4), (3 * H // 4, 3 * W // 4)][:n_flares]
    fire = (H // 2, W // 2)
    flare_ll = [px_to_latlon(box["bbox"], W, H, c, r) for r, c in flares]
    def get(layer, bbox, w, h, t, channel=None):
        if channel in (1, 2):
            return np.zeros((H, W), np.float32)
        a = np.full((H, W), 30.0, np.float32)
        for r, c in flares:
            a[r - 1:r + 2, c - 1:c + 2] += 90.0          # platform flares, every picture
        a[fire[0] - 1:fire[0] + 2, fire[1] - 1:fire[1] + 2] += 20.0   # the fire, lit before dark
        return a
    def area(lat, lon, *a, **k):     # NASA's long-term map knows the flares' fields, not the open water
        return {"flare_km": 0.5, "spot_days": 60} if any(haversine_km(lat, lon, *ll) <= 6 for ll in flare_ll) else None
    pics = Mock(); pics.get.side_effect = get
    open_sea = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
    with patch.dict(seawatch._SEA, {}, clear=True), \
            patch.object(seawatch, "land_mask_points", return_value=open_sea), \
            patch.object(seawatch, "sun_elevation", return_value=-30.0), \
            patch.object(seawatch, "_ir105_change", return_value=0.7), \
            patch.object(patterns, "platform_area", area):
        spots, notes = seawatch.find_spots(cfg, pics, box, WHEN, mem=None, geo=None)
    return [sp for sp in spots if sp.get("steady")], notes.get("rejected")

for n in (0, 2, 3, 4):
    steady, rejected = run(n)
    print(f"{n} platform flares in the box -> the open-water fire told: {bool(steady)}   rejected: {rejected}")
