"""follow.py against reading.py, near_posted and a second posted fire. Real follow.run/note/due/message,
reading.make, brain.near_posted; only the pictures are synthetic (as in tests/test_follow.py)."""
import copy, datetime as dt, math
from unittest.mock import patch
import numpy as np
from rasad import engine, follow, patterns, seawatch, reading, brain
from rasad.util import UTC, iso, parse_time, haversine_km

def config():
    cfg = copy.deepcopy(engine.DEFAULTS)
    cfg["follow"] = {"enabled": True}
    cfg["reading"] = {"enabled": True, "post": True}
    cfg["local_time"] = {"offset_hours": 3, "name": "بغداد"}
    return cfg

class Sky:
    def __init__(self, spots): self.spots = spots       # time -> [(lat, lon, above), ...]
    def get(self, layer, bbox, W, H, t, channel=None):
        img = np.full((H, W), 12.0, dtype=np.float32)
        for lat, lon, above in self.spots(t):
            if channel in (None, 0):
                w, s, e, n = bbox
                r = int((n - lat) / (n - s) * H); c = int((lon - w) / (e - w) * W)
                if 1 <= r < H - 1 and 1 <= c < W - 1:
                    img[r - 1:r + 2, c - 1:c + 2] += above
        return img

def everywhere_sea(bbox, W, H, coast_km, km):
    return np.ones((H, W), dtype=bool)

def east(lat, lon, km):
    return lat, lon + km / (111.32 * math.cos(math.radians(lat)))

with patch.object(seawatch, "sea_grid", everywhere_sea), patch.object(patterns, "_atlas", {}):
    cfg = config()
    # ---------- 1) the reply «ما زالت الحرارة ظاهرة», then the reading «ثم لم تعد تظهر»
    LAST = dt.datetime(2026, 11, 25, 19, 0, tzinfo=UTC)       # the finder's last sighting (22:00 Baghdad)
    P = (26.33, 56.25)
    X = {"id": "X", "status": "alerted", "tg_msg": 10, "place": "sea", "lat": P[0], "lon": P[1], "kind": "new",
         "first": iso(LAST - dt.timedelta(minutes=60)), "last": iso(LAST), "families": ["mtg"], "srcs": ["mtg_spot"],
         "polar": [], "geo_slots": [iso(LAST - dt.timedelta(minutes=10 * k)) for k in range(6, -1, -1)],
         "observations": [{"src": "mtg_spot", "t": iso(LAST - dt.timedelta(minutes=10 * k)), "lat": P[0], "lon": P[1]}
                          for k in range(6, -1, -1)]}
    sky = Sky(lambda t: [(P[0], P[1], 18.0)])                  # the heat is still there in every picture
    now = LAST + dt.timedelta(minutes=5)
    while now <= LAST + dt.timedelta(minutes=75):
        follow.run(cfg, None, [X], now, pics=sky)
        if follow.due(X, cfg):
            print(f"[{now + dt.timedelta(hours=3):%H:%M} Baghdad] REPLY under the post:")
            print("   " + follow.message(X, cfg).replace("\n", "\n   "))
            X["still_posted"] = iso(now)
        out = reading.make(X, cfg, now)
        if out and reading.due(X, cfg, now) and not X.get("reading_posted"):
            print(f"[{now + dt.timedelta(hours=3):%H:%M} Baghdad] READING under the same post:")
            print("   " + out[0])
            X["reading_posted"] = iso(now)
        now += dt.timedelta(minutes=10)
    print("   follow track at the time of the reading:", [r[0][11:16] for r in X["follow"]["track"]])

    # ---------- 2) near_posted does not know the follow track
    print()
    cfg = config()
    X2 = copy.deepcopy(X); X2.pop("follow", None); X2.pop("still_posted", None); X2.pop("reading_posted", None)
    # followed all day, drifting 10 km east; track's last sighting 70 minutes before the new sighting
    T_new = LAST + dt.timedelta(hours=13)
    drift = east(P[0], P[1], 10.0)
    X2["follow"] = {"track": [[iso(T_new - dt.timedelta(minutes=70 + 10 * k)), round(drift[0], 4), round(drift[1], 4), 9.0]
                              for k in range(5, -1, -1)], "streak": 6}
    Y = {"id": "Y", "status": "new", "place": "sea", "lat": drift[0], "lon": east(*drift, 1.0)[1], "first": iso(T_new),
         "last": iso(T_new), "observations": [{"src": "mtg_spot", "t": iso(T_new), "lat": drift[0], "lon": east(*drift, 1.0)[1]}]}
    print("new sighting 1 km from where X's heat was followed to 70 min before; near_posted ->", brain.near_posted(Y, [X2, Y]))
    T_new = LAST + dt.timedelta(hours=10)
    X2["follow"]["track"] = [[iso(T_new - dt.timedelta(minutes=70)), round(drift[0], 4), round(drift[1], 4), 9.0]]
    Y.update(first=iso(T_new), last=iso(T_new)); Y["observations"][0]["t"] = iso(T_new)
    np_ = brain.near_posted(Y, [X2, Y])
    print("same, 10 h after X's own last sighting; near_posted ->", np_)
    if np_:
        print("   line:", brain.near_posted_line(np_, cfg))

    # ---------- 3) another posted fire 4 km away, still burning, is taken for this fire's heat
    print()
    cfg = config()
    A = copy.deepcopy(X); [A.pop(k, None) for k in ("follow", "still_posted", "reading_posted")]; A["id"] = "A (went out)"
    Bpos = east(P[0], P[1], 4.0)
    Bf = {"id": "B (still burning)", "status": "alerted", "tg_msg": 11, "place": "sea", "lat": Bpos[0], "lon": Bpos[1],
          "first": iso(LAST - dt.timedelta(minutes=60)), "last": iso(LAST), "observations": []}
    sky = Sky(lambda t: [(Bpos[0], Bpos[1], 25.0)])           # only B's heat is in the pictures
    now = LAST + dt.timedelta(minutes=35)
    for step in range(6):
        t_pic = follow.floor_time(now, 10)
        Bf["observations"].append({"src": "mtg_spot", "t": iso(t_pic), "lat": Bpos[0], "lon": Bpos[1]})   # B's own sighting
        Bf["last"] = iso(t_pic)
        follow.run(cfg, None, [A, Bf], now, pics=sky)
        if follow.due(A, cfg):
            print(f"[{now + dt.timedelta(hours=3):%H:%M} Baghdad] REPLY under A's post (A itself is out):")
            print("   " + follow.message(A, cfg).replace("\n", "\n   "))
            A["still_posted"] = iso(now)
            break
        now += dt.timedelta(minutes=10)
    # ...and (HEAD) B's next own sighting: which fire does find_incident give it to?
    I = cfg["incidents"]
    det = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(follow.floor_time(now, 10) + dt.timedelta(minutes=10)),
           "lat": round(Bpos[0], 4), "lon": round(Bpos[1], 4)}
    got = brain.find_incident([A, Bf], det, float(I["merge_km"]), float(I["merge_hours"]), rules=I)
    print("B's next sighting at B's own place is given to:", got["id"] if got else None,
          "| A.follow.track last:", A["follow"]["track"][-1][:3], "| B at", [round(v, 4) for v in Bpos])
