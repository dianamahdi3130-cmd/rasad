"""Scenario: a fire lit before dark (steady rule finds its west end B, posted as X); later its east
end A (4 km) flares: the regular finder finds A for an hour, then loses it ('hot before'); the
steady rule then tells B again with its two back-filled pictures. Real find_spots / watch /
find_incident; only pictures, sun, land mask are synthetic."""
import copy, datetime as dt, math, sys
from contextlib import contextmanager
from unittest.mock import Mock, patch
import numpy as np
from rasad import engine, seawatch, brain, patterns
from rasad.util import box_around, px_to_latlon, iso, parse_time, haversine_km

UTC = dt.timezone.utc
cfg = copy.deepcopy(engine.DEFAULTS)
SW = cfg["seawatch"]
SW["steady_min_above"] = 8          # as in config.yaml
SW["daytime"]["enabled"] = False
box = {"id": "hormuz_oman", "bbox": box_around(26.33, 56.25, 30)}
SW["boxes"] = [box]
SW["priority_boxes"] = ["hormuz_oman"]
km = SW["km_per_px"]
W, H = seawatch.box_size(box, km)
r, cW = H // 2, W // 2 - 8
cE = cW + 16                          # 16 px = 4 km east
B = px_to_latlon(box["bbox"], W, H, cW, r)
A = px_to_latlon(box["bbox"], W, H, cE, r)
print("B (west end, steady):", [round(x, 4) for x in B], " A (east end):", [round(x, 4) for x in A],
      " apart km:", round(haversine_km(*B, *A), 2))
print("platform_area at B:", patterns.platform_area(*B))

T1 = dt.datetime(2026, 11, 25, 18, 0, tzinfo=UTC)     # the east end lights up
WEST, EAST = 25.0, 24.0

def picture(t, channel):
    a = np.full((H, W), 30.0, np.float32)
    if channel in (1, 2):
        return np.zeros((H, W), np.float32)
    flip = (t.minute // 10) % 2 == 1 and t >= T1
    a[r - 1:r + 2, cW - 1:cW + 2] += (WEST - 3 if flip else WEST)
    a[r - 1:r + 2, cE - 1:cE + 2] += EAST      # both ends of the streak hot since daylight
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

def judge(rows, now):
    made = []
    for d in sorted(rows, key=lambda d: d["t"]):
        inc = brain.find_incident(incidents, d, float(I["merge_km"]), float(I["merge_hours"]), rules=I)
        if inc is None:
            inc = brain.new_incident(d, "new")
            inc["known_days"] = 0
            incidents.append(inc)
            made.append(inc["id"])
        brain.add_to_incident(inc, d)
    for inc in incidents:                     # what enrich/finalize would give at sea
        inc["place"] = "sea"
        inc["verdict"], inc["reason"] = brain.verdict_of(inc)
    return made

with patch.dict(seawatch._SEA, {}, clear=True), \
        patch.object(seawatch, "land_mask_points", return_value=open_sea), \
        patch.object(seawatch, "sun_elevation", return_value=-30.0), \
        patch.object(seawatch, "_ir105_change", return_value=0.7), \
        patch.object(seawatch, "Pictures", Pics):
    now = T1 - dt.timedelta(minutes=29)
    while now <= T1 + dt.timedelta(minutes=95):
        rows = seawatch.watch(cfg, Mock(), state, now, None, None, Mock(has=lambda *a, **k: True))
        new = [d for d in rows]
        made = judge(rows, now)
        newest = max((d["t"] for d in rows), default=None)
        cur = [(d["t"][11:16], "B" if haversine_km(d["lat"], d["lon"], *B) < 1 else "A" if haversine_km(d["lat"], d["lon"], *A) < 1 else "?",
                "steady" if d.get("steady") else "finder") for d in rows if d["t"] >= iso(now - dt.timedelta(minutes=31))]
        print(f"check {now:%H:%M}: rows(last 30 min) {sorted(set(cur))}" + (f"  NEW INCIDENT {made}" if made else ""))
        if len(incidents) == 1 and incidents[0].get("status") != "alerted":
            incidents[0].update(status="alerted", tg_msg=1, alerted_at=iso(now), alerted_verdict=incidents[0]["verdict"])
            print("   -> X posted:", incidents[0]["id"], incidents[0]["verdict"])
        now += dt.timedelta(minutes=10)

print()
for inc in incidents:
    np_ = brain.near_posted(inc, incidents) if inc.get("status") != "alerted" else None
    print(inc["id"], inc.get("status"), inc["verdict"], "first", inc["first"][11:16], "last", inc["last"][11:16],
          "slots", len(inc["geo_slots"]), "| alert_worthy at sea:", True if inc["geo_slots"] else None,
          "| near_posted:", np_)
    if np_:
        full = copy.deepcopy(cfg); full["local_time"] = {"offset_hours": 3, "name": "بغداد"}
        print("   line:", brain.near_posted_line(np_, full))
