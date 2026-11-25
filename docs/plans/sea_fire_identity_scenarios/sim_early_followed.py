"""HEAD (c7e3553): «a first sighting on the followed heat is not a first sighting». Does the channel's
first-sighting path (earlyalerts.sync) see the followed fire?"""
import copy, datetime as dt
from types import SimpleNamespace
from unittest.mock import patch
from rasad import engine, earlyalerts, preliminary
from rasad.util import iso

UTC = dt.timezone.utc
cfg = copy.deepcopy(engine.DEFAULTS)
cfg["local_time"] = {"offset_hours": 3, "name": "بغداد"}
cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.0, 25.0, 57.5, 27.5], "enabled": True}]
when = dt.datetime(2026, 11, 25, 16, 10, tzinfo=UTC)
now = when + dt.timedelta(minutes=4)
X = {"id": "X", "status": "alerted", "tg_msg": 1325, "place": "sea", "lat": 26.406, "lon": 56.2555,
     "first": "2026-11-24T20:30:00Z", "last": "2026-11-25T00:30:00Z",
     "observations": [{"src": "mtg_spot", "t": "2026-11-25T00:30:00Z", "lat": 26.3498, "lon": 56.2431}],
     "follow": {"track": [["2026-11-25T16:00:00Z", 26.33, 56.275, 9.0]], "streak": 9, "looked": "2026-11-25T16:00:00Z"}}
state = {"sea_candidates": [{"t": iso(when), "lat": 26.335, "lon": 56.29, "rise": 22.0, "src": "mtg_spot",
                             "strong": True, "flame": True}]}
S = SimpleNamespace(state=state, incidents=[X])
with patch("rasad.preliminary.sun_elevation", return_value=-30.0):
    print("preliminary._represented (what the test checks):", preliminary._represented(26.335, 56.29, when, [X]))
    print("first-sighting records made by earlyalerts.sync:", earlyalerts.sync(cfg, S, now))
    for rec in state.get("early_night", {}).values():
        print("   would be posted:", earlyalerts._message(cfg, rec).split("\n")[0], "|", rec["lat"], rec["lon"])
