"""A fire the steady rule posted (X, at the west end B of its streak). Its east end A (4 km) flares with
a flame's glow: is the single picture posted as a first sighting «رصد حراري أولي»? Real find_spots,
watch, earlyalerts.sync; synthetic pictures."""
import copy, datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from rasad import engine, seawatch, brain, earlyalerts
from rasad.util import box_around, px_to_latlon, iso, haversine_km

UTC = dt.timezone.utc
cfg = copy.deepcopy(engine.DEFAULTS)
cfg["local_time"] = {"offset_hours": 3, "name": "بغداد"}
SW = cfg["seawatch"]; SW["steady_min_above"] = 8; SW["daytime"]["enabled"] = False
box = {"id": "hormuz_oman", "bbox": box_around(26.33, 56.25, 30)}
SW["boxes"] = [box]; SW["priority_boxes"] = ["hormuz_oman"]
cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.0, 25.0, 57.5, 27.5], "enabled": True}]
W, H = seawatch.box_size(box, SW["km_per_px"])
r, cW = H // 2, W // 2 - 8; cE = cW + 16
B = px_to_latlon(box["bbox"], W, H, cW, r); A = px_to_latlon(box["bbox"], W, H, cE, r)
T1 = dt.datetime(2026, 11, 25, 18, 0, tzinfo=UTC)

def picture(t, channel):
    if channel in (1, 2):
        g = np.zeros((H, W), np.float32)
        if t >= T1:
            g[r - 1:r + 2, cE - 1:cE + 2] = 8.0       # a flame's glow at the east end
        return g
    a = np.full((H, W), 30.0, np.float32)
    a[r - 1:r + 2, cW - 1:cW + 2] += 28.0             # steady since daylight (shown by the closer look: >= 25)
    if t >= T1:
        a[r - 1:r + 2, cE - 1:cE + 2] += 26.0
    return a

class Pics:
    def __init__(self, *a, **k): self.cache = {}
    def get(self, layer, bbox, w, h, t, channel=None): return picture(t, channel)
    def prefetch(self, reqs): pass
    @contextmanager
    def cached_only(self):
        yield self

open_sea = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
state, incidents = {}, []
I = cfg["incidents"]
with patch.dict(seawatch._SEA, {}, clear=True), \
        patch.object(seawatch, "land_mask_points", return_value=open_sea), \
        patch.object(seawatch, "sun_elevation", return_value=-30.0), \
        patch.object(seawatch, "_ir105_change", return_value=0.7), \
        patch.object(seawatch, "Pictures", Pics), \
        patch("rasad.preliminary.sun_elevation", return_value=-30.0):
    for now in (T1 - dt.timedelta(minutes=9), T1 + dt.timedelta(minutes=2)):
        rows = seawatch.watch(cfg, Mock(), state, now, None, None, Mock(has=lambda *a, **k: True))
        for d in sorted(rows, key=lambda d: d["t"]):
            inc = brain.find_incident(incidents, d, float(I["merge_km"]), float(I["merge_hours"]), rules=I)
            if inc is None:
                inc = brain.new_incident(d, "new"); incidents.append(inc)
            brain.add_to_incident(inc, d)
        for inc in incidents:
            inc.update(place="sea", status="alerted", tg_msg=1, alerted_at=iso(now), alerted_verdict="مرجّح")
        S = SimpleNamespace(state=state, incidents=incidents)
        added = earlyalerts.sync(cfg, S, now)
        cand = [c for c in state["sea_candidates"] if c["t"] == iso(T1)]
        print(f"check {now:%H:%M}: incidents {[(i['id'], i['last'][11:16]) for i in incidents]}; finder candidates of 18:00: "
              f"{[(round(haversine_km(c['lat'], c['lon'], *A), 1), 'km from A', 'strong' if c.get('strong') else 'faint') for c in cand]}; "
              f"first-sighting records added: {added}")
    for rec in state.get("early_night", {}).values():
        print("WOULD BE POSTED NOW (earlyalerts.deliver):")
        print("   " + earlyalerts._message(cfg, rec).replace("\n", "\n   "))
        print("   km from the posted fire's spot B:", round(haversine_km(rec["lat"], rec["lon"], *B), 1))
