"""A steady find the closer look refused (unseen_close). Later the same place brightens and the regular
finder sees it in two pictures: what becomes of those sightings?"""
import copy, datetime as dt
from rasad import engine, brain
from rasad.util import iso
UTC = dt.timezone.utc
cfg = copy.deepcopy(engine.DEFAULTS); I = cfg["incidents"]
P = (26.3311, 56.2290)
def row(t, **k): return dict({"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(t), "lat": P[0], "lon": P[1], "frp": None}, **k)
t0 = dt.datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
Y = brain.new_incident(row(t0), "new")
for k in range(0, 30):
    brain.add_to_incident(Y, row(t0 + dt.timedelta(minutes=10 * k)))
Y.update(place="sea", kind="new", known_days=0, facility=None, unseen_close=True, status="new")   # the closer look said no
print("before:", brain.verdict_of(Y)[0], "| worth an alert:", brain.alert_worthy(Y, cfg))
for k in (36, 37, 38):     # 20:00-20:20 UTC: flames, a rise of 12 with a glow, found by the regular finder 0.5 km away
    d = row(t0 + dt.timedelta(minutes=10 * k), lat=P[0] + 0.004, flame=True)
    inc = brain.find_incident([Y], d, float(I["merge_km"]), float(I["merge_hours"]), rules=I)
    print("finder sighting", d["t"][11:16], "->", "joins the doubted record" if inc is Y else "new incident")
    brain.add_to_incident(inc or Y, d)
print("after:", brain.verdict_of(Y)[0], "|", brain.verdict_of(Y)[1], "| worth an alert:", brain.alert_worthy(Y, cfg))
