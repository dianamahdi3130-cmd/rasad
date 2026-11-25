import copy, datetime as dt, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, "/home/user/rasad")
from rasad import earlyalerts, engine, preliminary
from rasad.util import UTC, iso

NOW = dt.datetime(2026, 11, 25, 19, 0, tzinfo=UTC)
WHEN = NOW - dt.timedelta(minutes=10)
d = tempfile.mkdtemp()
cfg = copy.deepcopy(engine.DEFAULTS)
cfg.update(_site_dir=Path(d) / "site", _state_dir=Path(d) / "state")
cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 22.4, 60.2, 27.2]}]
cfg["seawatch"]["early_alerts"] = dict(earlyalerts.DEFAULTS, enabled=True, strong_only=True)
cfg["follow"] = {"enabled": True}
print("early enabled:", earlyalerts.enabled(cfg), "sw.alerts:", cfg["seawatch"].get("alerts"), cfg["seawatch"].get("enabled"))
cand = {"src": "mtg_spot", "t": iso(WHEN), "lat": 26.3716, "lon": 56.2988, "rise": 53.3, "strong": True,
        "reference_minutes": [30],
        "source_grid": {"box_id": "hormuz_oman", "bbox": [55, 22.4, 60.2, 27.2], "width": 1051, "height": 1069,
                        "km_per_px": .5, "layer": "mtg_fd:rgb_firetemperature"}}
# a posted sea fire whose heat was followed to this very place in the last hour (same picture too)
inc = {"id": "20261124-2030_26.406_56.255", "status": "alerted", "tg_msg": 1325, "place": "sea",
       "alerted_verdict": "مرجّح", "alerted_at": "2026-11-24T21:10:00Z",
       "lat": 26.406, "lon": 56.2555, "first": "2026-11-24T20:30:00Z", "last": "2026-11-25T00:30:00Z",
       "observations": [{"src": "mtg_spot", "t": "2026-11-25T00:30:00Z", "lat": 26.3498, "lon": 56.2431}],
       "follow": {"track": [[iso(WHEN - dt.timedelta(minutes=20)), 26.3716, 56.2990, 9.0],
                            [iso(WHEN - dt.timedelta(minutes=10)), 26.3716, 56.2990, 9.0],
                            [iso(WHEN), 26.3716, 56.2990, 9.0]], "streak": 3, "looked": iso(WHEN)},
       "still_posted": "2026-11-25T08:00:00Z"}
S = SimpleNamespace(state={"sea_candidates": [cand]}, incidents=[inc], memory={}, sent=False)
print("represented (app list):", preliminary._represented(cand["lat"], cand["lon"], WHEN, [inc]))
print("sync added:", earlyalerts.sync(cfg, S, NOW))
post = Mock(return_value=999)
n = earlyalerts.deliver(cfg, S, NOW, post, Mock(return_value=False))
print("deliver sent:", n)
if post.call_args:
    print(post.call_args.args[0])
    print("reply_to:", post.call_args.kwargs.get("reply_to"))
