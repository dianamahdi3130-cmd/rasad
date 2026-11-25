"""A new fire 12 km from a posted sea fire that went out 4 hours earlier: is it absorbed?"""
import copy, datetime as dt, sys
from unittest.mock import patch
sys.path.insert(0, "/home/user/rasad"); sys.path.insert(0, "/home/user/rasad/tests")
import numpy as np
from rasad import brain, engine, follow, patterns, seawatch
from rasad.util import UTC, iso, haversine_km
import test_follow as TF

cfg = TF.config()
inc = TF.posted()            # last own sighting 00:30 UTC at 26.3498, 56.2431
B = (26.30, 56.36)           # another vessel
print("B is %.1f km from A's last sighting, %.1f km from A's anchor" % (
    haversine_km(TF.LAT, TF.LON, *B), haversine_km(inc["lat"], inc["lon"], *B)))
T = dt.datetime(2026, 11, 25, 4, 30, tzinfo=UTC)     # 4 h after A's last sighting
spots = {T: (B[0], B[1], 20.0), T + dt.timedelta(minutes=10): (B[0], B[1], 20.0)}
sky = TF.Sky(cfg, spots)
with patch.object(seawatch, "sea_grid", TF.everywhere_sea), patch.object(patterns, "_atlas", {}):
    t = dt.datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
    while t <= T:
        follow.run(cfg, None, [inc], t, pics=sky)
        t += dt.timedelta(minutes=10)
print("follow after the check that read picture T:", inc["follow"])
print("due (reply)?", follow.due(inc, cfg))
# the night finder's first pair of B (pictures T-0 and T+10 arrive at the next check) / a candidate at T
for det in ({"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(T), "lat": B[0], "lon": B[1]},
            {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": iso(T + dt.timedelta(minutes=3)),
             "lat": B[0] + 0.01, "lon": B[1] + 0.01, "frp": 40.0, "conf": "h"}):
    got = brain.find_incident([inc], dict(det), 6, 12, rules=cfg["incidents"])
    print(det["src"], "->", "MERGED into the old fire" if got is inc else got)
    old = brain.find_incident([dict(inc, follow={})], dict(det), 6, 12, rules=cfg["incidents"])
    print("   without the follow track:", "merged" if old is not None else "a new incident (posted with the near_posted note)")
