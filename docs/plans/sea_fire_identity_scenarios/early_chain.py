import copy, datetime as dt, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, "/home/user/rasad")
from rasad import brain, earlyalerts, engine
from rasad.util import UTC, iso

NOW = dt.datetime(2026, 11, 25, 19, 0, tzinfo=UTC)
WHEN = NOW - dt.timedelta(minutes=10)
d = tempfile.mkdtemp()
cfg = copy.deepcopy(engine.DEFAULTS)
cfg.update(_site_dir=Path(d) / "site", _state_dir=Path(d) / "state")
cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55, 22.4, 60.2, 27.2]}]
cfg["seawatch"]["early_alerts"] = dict(earlyalerts.DEFAULTS, enabled=True, strong_only=True)
cand = {"src": "mtg_spot", "t": iso(WHEN), "lat": 26.3716, "lon": 56.2988, "rise": 53.3, "strong": True, "flame": True}
inc = {"id": "20261124-2030_26.406_56.255", "kind": "new", "status": "alerted", "tg_msg": 1325, "place": "sea",
       "alerted_verdict": "مرجّح", "alerted_at": "2026-11-24T21:10:00Z", "best": "geo", "n": 2,
       "lat": 26.406, "lon": 56.2555, "first": "2026-11-24T20:30:00Z", "last": "2026-11-25T00:30:00Z",
       "families": ["mtg"], "srcs": ["mtg_spot"], "geo_slots": ["2026-11-24T20:30:00Z", "2026-11-25T00:30:00Z"], "polar": [], "max_frp": None,
       "observations": [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": "2026-11-25T00:30:00Z", "lat": 26.3498, "lon": 56.2431}],
       "early_ids": ["candidate-20261124-2030_26.4060_56.2555"],      # this fire was itself first told as a first sighting
       "follow": {"track": [[iso(WHEN), 26.3716, 56.2990, 9.0]], "streak": 1, "looked": iso(WHEN)}}
S = SimpleNamespace(state={"sea_candidates": [cand]}, incidents=[inc], memory={}, sent=False)
earlyalerts.sync(cfg, S, NOW)
post = Mock(return_value=999)
print("new top-level first-sighting posts:", earlyalerts.deliver(cfg, S, NOW, post, Mock(return_value=False)))
# ten minutes later the finder's pair comes: both pictures are dets, and they continue the followed fire
LATER = NOW + dt.timedelta(minutes=10)
for t in (WHEN, WHEN + dt.timedelta(minutes=10)):
    det = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(t), "lat": 26.3716, "lon": 56.2988, "frp": None}
    got = brain.find_incident(S.incidents, det, 6, 12, rules=cfg["incidents"])
    print("pair det", iso(t), "->", got["id"] if got else None)
    brain.add_to_incident(got, det)
earlyalerts.sync(cfg, S, LATER)
print("early_ids:", inc.get("early_ids"))
print("early_promotion_ambiguous:", inc.get("early_promotion_ambiguous"), "(engine then skips this incident before any update)")
