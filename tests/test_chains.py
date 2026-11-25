"""One fire at sea, one marker (the app only): which records are the same heat, which may be, which
are not (chains.py). The case: one burning ship off Khasab, 23-25 Nov 2026, posted six times; the
app showed four markers and the owner asked "why four fires?"."""
import copy
import unittest
from unittest.mock import patch

from rasad import chains, engine


def record(rid, rows, **extra):
    obs = [{"t": t, "lat": la, "lon": lo, "src": "mtg_spot"} for t, la, lo in rows]
    inc = {"id": rid, "place": "sea", "headline": "حريق في البحر قرب مضيق هرمز", "first": rows[0][0], "last": rows[-1][0],
           "lat": rows[0][1], "lon": rows[0][2], "observations": obs, "status": "alerted"}
    inc.update(extra)
    return inc


def annotate(incidents, reviewed=None):
    items = [{"id": i["id"], "place": i["place"]} for i in incidents]
    before = copy.deepcopy(incidents)
    with patch.object(chains, "_reviewed", reviewed or {}):
        chains.annotate(items, incidents, copy.deepcopy(engine.DEFAULTS))
    assert incidents == before, "the stored records are never touched"
    return {i["id"]: i for i in items}


NIGHT = record("night", [("2026-11-24T20:30:00Z", 26.406, 56.2555), ("2026-11-25T00:30:00Z", 26.3498, 56.2431)])
NOON = record("noon", [("2026-11-25T10:02:00Z", 26.2893, 56.1398)])
DUSK = record("dusk", [("2026-11-25T14:40:00Z", 26.3161, 56.2629), ("2026-11-25T17:10:00Z", 26.3453, 56.3025)])
LATE = record("late", [("2026-11-25T17:10:00Z", 26.3431, 56.2629), ("2026-11-25T17:50:00Z", 26.3588, 56.2753)])
DAY_TRACK = {"noon": [["2026-11-25T05:10:00Z", 26.3351, 56.1788], ["2026-11-25T13:50:00Z", 26.3152, 56.2388]]}


class OneFireOneMarker(unittest.TestCase):
    def test_the_hormuz_records_as_the_tool_holds_them(self):
        out = annotate([NIGHT, NOON, DUSK, LATE], DAY_TRACK)
        # the heat was seen in every picture from the noon record to the dusk one (the reviewed track),
        # and the late record is the other end of the dusk record's streak in the same picture
        for rid in ("noon", "dusk", "late"):
            self.assertEqual((out[rid]["chain"]["root"], out[rid]["chain"]["n"]), ("noon", 3), rid)
        self.assertEqual(out["noon"]["chain"]["since"], "2026-11-25T05:10:00Z")
        # between the night fire and the morning's heat: 4.7 hours with nothing, 7 km apart
        tie = out["noon"]["chain"]["maybe"]
        self.assertEqual((tie["prev"], tie["gap_h"]), ("night", 4.7))
        self.assertIn("قد يكون الحريق نفسه وقد يكون حريقاً آخر", tie["text"])
        self.assertNotIn("chain", out["night"], "the night fire keeps its own marker")
        self.assertEqual(len(out["night"]["path"]), 2, "a fire that moved shows its path")

    def test_without_the_pictures_between_the_tie_is_not_settled(self):
        out = annotate([NOON, DUSK])
        self.assertEqual(out["dusk"]["chain"]["maybe"]["prev"], "noon", "4.6 hours unseen, 12.5 km: within a hull's drift")
        self.assertNotIn("root", out["dusk"]["chain"])

    def test_two_at_once_and_too_far_are_other_fires(self):
        other = record("other", [("2026-11-25T17:10:00Z", 26.25, 56.45)])       # the same picture, 18 km away
        far = record("far", [("2026-11-25T18:30:00Z", 27.10, 56.90)])           # 100 km an hour later
        long_after = record("after", [("2026-11-26T12:00:00Z", 26.3453, 56.3025)])   # 19 hours later, the same place
        out = annotate([DUSK, other, far, long_after])
        for rid in ("other", "far", "after"):
            self.assertNotIn("chain", out[rid], rid)

    def test_followed_heat_ties_a_later_record_to_the_fire(self):
        dusk = copy.deepcopy(DUSK)
        dusk["follow"] = {"track": [["2026-11-25T19:40:00Z", 26.36, 56.34, 6.0], ["2026-11-25T19:50:00Z", 26.362, 56.345, 5.0]]}
        again = record("again", [("2026-11-25T20:30:00Z", 26.37, 56.36)])
        out = annotate([dusk, again])
        self.assertEqual(out["again"]["chain"]["root"], "dusk", "seen 40 minutes before, 2 km away")
        self.assertEqual(out["dusk"]["path"][-1][1:], [26.362, 56.345], "the path goes on where the heat was followed")

    def test_only_the_sea_and_only_when_switched_on(self):
        land = record("land", [("2026-11-25T14:40:00Z", 26.3161, 56.2629)], place="land")
        self.assertEqual(annotate([DUSK, land])["land"], {"id": "land", "place": "land"})
        items = [{"id": "dusk", "place": "sea"}, {"id": "late", "place": "sea"}]
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["chains"] = {"enabled": False}
        self.assertEqual(chains.annotate(items, [DUSK, LATE], cfg), 0)
        self.assertNotIn("chain", items[1])

    def test_the_reviewed_track_of_25_november_is_read(self):
        with patch.object(chains, "_reviewed", None):
            rows = chains.reviewed_tracks()["20261125-1002_26.289_56.140"]
        self.assertGreater(len(rows), 40)
        self.assertEqual(rows[0][0], "2026-11-25T05:10:00Z")


if __name__ == "__main__":
    unittest.main()
