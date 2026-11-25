"""A steady source in open water told by the steady rule all night (posted as X). Next evening the rule
tells it again at the very same place: same fire or a new post? Real find_incident / near_posted."""
import copy, datetime as dt
from rasad import engine, brain
from rasad.util import iso
UTC = dt.timezone.utc
I = engine.DEFAULTS["incidents"]
P = (26.3311, 56.2290)
def row(t): return {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": iso(t), "lat": P[0], "lon": P[1], "frp": None}
# the night of 25-26 Nov in the Hormuz box: the rule's last picture is 01:50 UTC, its first of the next
# evening 14:20 UTC with the back-filled sightings 14:00 and 14:10 (computed from seawatch._night)
night = [dt.datetime(2026, 11, 25, 14, 0, tzinfo=UTC) + dt.timedelta(minutes=10 * k) for k in range(0, 72)]
X = brain.new_incident(row(night[0]), "new")
for t in night:
    brain.add_to_incident(X, row(t))
X.update(place="sea", status="alerted", tg_msg=1, alerted_at=iso(night[3]), verdict="مرجّح")
print("X last sighting:", X["last"])
incidents = [X]
for t in (dt.datetime(2026, 11, 26, 14, 0, tzinfo=UTC), dt.datetime(2026, 11, 26, 14, 10, tzinfo=UTC), dt.datetime(2026, 11, 26, 14, 20, tzinfo=UTC)):
    d = row(t)
    inc = brain.find_incident(incidents, d, float(I["merge_km"]), float(I["merge_hours"]), rules=I)
    if inc is None:
        inc = brain.new_incident(d, "new"); incidents.append(inc); print("NEW INCIDENT", inc["id"], "for the sighting of", d["t"])
    brain.add_to_incident(inc, d)
Y = incidents[-1]; Y["place"] = "sea"
print("incidents:", [(i["id"], i["first"], i["last"]) for i in incidents])
print("verdict of the new one:", brain.verdict_of(Y)[0], "| near_posted line for its post:", brain.near_posted(Y, incidents))
