"""follow: the reply's «الموضع الذي رُصد فيه آخر مرة عند HH:MM» after the fire's own sightings resumed."""
import copy, datetime as dt, math
from unittest.mock import patch
import numpy as np
from rasad import engine, follow, patterns, seawatch
from rasad.util import UTC, iso

cfg = copy.deepcopy(engine.DEFAULTS); cfg["follow"] = {"enabled": True}
cfg["local_time"] = {"offset_hours": 3, "name": "بغداد"}
def east(lat, lon, km): return lat, lon + km / (111.32 * math.cos(math.radians(lat)))
P0 = (26.35, 56.24); P1 = east(*P0, 8.0)
T0 = dt.datetime(2026, 11, 25, 0, 30, tzinfo=UTC)     # own last sighting of the night (03:30 Baghdad)
spots = {}
spots[T0 + dt.timedelta(minutes=40)] = (P0[0], P0[1], 8.0)              # one faint follow hit at dawn, then nothing
for k in range(0, 7):                                                    # evening: the heat is at P1 all the time
    spots[dt.datetime(2026, 11, 25, 17, 0, tzinfo=UTC) + dt.timedelta(minutes=10 * k)] = (P1[0], P1[1], 15.0)

class Sky:
    def get(self, layer, bbox, W, H, t, channel=None):
        img = np.full((H, W), 12.0, dtype=np.float32)
        s = spots.get(t)
        if s and channel in (None, 0):
            w, so, e, n = bbox
            r = int((n - s[0]) / (n - so) * H); c = int((s[1] - w) / (e - w) * W)
            if 1 <= r < H - 1 and 1 <= c < W - 1:
                img[r - 1:r + 2, c - 1:c + 2] += s[2]
        return img

inc = {"id": "X", "status": "alerted", "tg_msg": 1, "place": "sea", "lat": P0[0], "lon": P0[1],
       "first": "2026-11-24T20:30:00Z", "last": iso(T0),
       "observations": [{"t": "2026-11-24T20:30:00Z", "lat": P0[0], "lon": P0[1]}, {"t": iso(T0), "lat": P0[0], "lon": P0[1]}]}
with patch.object(seawatch, "sea_grid", lambda *a: np.ones((a[2], a[1]), bool)), patch.object(patterns, "_atlas", {}):
    now = T0 + dt.timedelta(minutes=35)
    while now <= T0 + dt.timedelta(hours=2):                 # dawn: one hit, then misses
        follow.run(cfg, None, [inc], now, pics=Sky()); now += dt.timedelta(minutes=10)
    print("after dawn: from =", inc["follow"].get("from"), "streak =", inc["follow"]["streak"], "replied:", bool(inc.get("still_posted")))
    # evening: the finder (steady rule / a NASA pass joined by _followed_km or merge) sees the fire again at P1
    for k in range(0, 4):
        t = dt.datetime(2026, 11, 25, 16, 30, tzinfo=UTC) + dt.timedelta(minutes=10 * k)
        inc["observations"].append({"src": "mtg_spot", "t": iso(t), "lat": round(P1[0], 4), "lon": round(P1[1], 4)})
        inc["last"] = iso(t)
    now = dt.datetime(2026, 11, 25, 17, 35, tzinfo=UTC)
    while now <= dt.datetime(2026, 11, 25, 18, 5, tzinfo=UTC):
        follow.run(cfg, None, [inc], now, pics=Sky())
        if follow.due(inc, cfg):
            print(f"[{now + dt.timedelta(hours=3):%H:%M} Baghdad] REPLY (own last sighting was at 20:00 Baghdad, at this very place):")
            print("   " + follow.message(inc, cfg).split("\n")[1])
            break
        now += dt.timedelta(minutes=10)
