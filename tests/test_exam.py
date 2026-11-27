"""The exam (tools/exam.py): what a version would post, replayed on stored events."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import exam  # noqa: E402

BRAIN, SEAWATCH, CFG = exam.import_version(HERE)


def spot(t, lat=26.30, lon=56.30):
    return {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": lat, "lon": lon}


def pixel(t, frp, lat=26.283, lon=56.254, sat="VIIRS_NOAA20_NRT"):
    return {"src": sat, "family": "viirs", "kind": "polar", "t": t, "lat": lat, "lon": lon, "frp": frp,
            "conf": "n", "night": True, "scan": 0.38, "track": 0.38}


def incident(iid, rows, **facts):
    """An incident as the memory keeps it, built from its sightings."""
    inc = {"id": iid, "kind": "new", "status": "new", "alerted_verdict": None, "best": "geo",
           "place": "sea", "facility": None, "country": "", "regions": ["hormuz"], "region_name": "مضيق هرمز",
           "where": "في البحر", "known_days": 0, "area_days": 0, "typical_frp": None}
    inc.update(facts)
    for d in sorted(rows, key=lambda r: r["t"]):
        if "first" not in inc:
            base = BRAIN.new_incident(d, inc["kind"])
            base.update({k: v for k, v in inc.items() if k not in ("first", "last")})
            base["id"] = iid
            inc = base
        BRAIN.add_to_incident(inc, d)
    return inc


def events(*incs, early=()):
    return {"incidents": sorted(incs, key=lambda i: i["first"]), "early": list(early), "taken": []}


class Cut(unittest.TestCase):
    def test_only_the_sightings_up_to_the_cut(self):
        inc = incident("a", [spot("2026-11-26T19:00:00Z"), spot("2026-11-26T19:10:00Z"),
                             pixel("2026-11-26T22:10:00Z", 3.7)])
        c = exam.cut(inc, "2026-11-26T19:10:00Z")
        self.assertEqual(c["geo_slots"], ["2026-11-26T19:00:00Z", "2026-11-26T19:10:00Z"])
        self.assertEqual(c["srcs"], ["mtg_spot"])
        self.assertIsNone(c["max_frp"])
        self.assertEqual(c["polar"], [])
        self.assertEqual(exam.cut(inc, inc["last"])["max_frp"], 3.7)


class Decide(unittest.TestCase):
    def test_two_pictures_of_the_sea_finder_post_at_the_second(self):
        inc = incident("b", [spot("2026-11-27T15:30:00Z"), spot("2026-11-27T15:40:00Z")])
        d = exam.decide(events(inc), BRAIN, SEAWATCH, CFG)["incidents"]["b"]
        self.assertTrue(d["posted"])
        self.assertEqual(d["post_t"], "2026-11-27T15:52:00Z")     # the second picture and the finder's delay

    def test_nasa_at_night_in_open_water_posts_only_where_no_heat_was_seen(self):
        """The Khasab case of 26 Nov as the rules stand: one earlier day of heat (the ship of 23-25 Nov)
        holds NASA's two night pixels back."""
        rows = [pixel("2026-11-26T22:10:00Z", 3.18, lon=56.256), pixel("2026-11-26T22:10:00Z", 3.7)]
        fresh = incident("c", rows, known_days=0, area_days=9)
        seen = incident("d", rows, known_days=1, area_days=9)
        out = exam.decide(events(fresh, seen), BRAIN, SEAWATCH, CFG)["incidents"]
        self.assertTrue(out["c"]["posted"])
        self.assertFalse(out["d"]["posted"])
        self.assertEqual(out["d"]["why_not"], "verdict يحتاج تأكيداً")

    def test_judged_late_live_stays_late(self):
        inc = incident("e", [spot("2026-11-27T15:30:00Z"), spot("2026-11-27T15:40:00Z")], status="late")
        d = exam.decide(events(inc), BRAIN, SEAWATCH, CFG)["incidents"]["e"]
        self.assertFalse(d["posted"])

    def test_a_first_sighting_sent_before_the_incident_is_its_one_post(self):
        inc = incident("f", [spot("2026-11-24T18:50:00Z"), spot("2026-11-24T19:00:00Z")])
        early = {"id": "candidate-f", "first": "2026-11-24T18:50:00Z", "lat": 26.3, "lon": 56.3,
                 "sent_at": "2026-11-24T18:58:00Z", "promoted_to": "f"}
        cfg = dict(CFG, seawatch=dict(CFG["seawatch"], enabled=True, alerts=True,
                                      early_alerts=dict(CFG["seawatch"].get("early_alerts") or {}, enabled=True)))
        res = exam.decide(events(inc, early=[early]), BRAIN, SEAWATCH, cfg)
        self.assertEqual(res["incidents"]["f"].get("posted_by_early"), "candidate-f")
        self.assertEqual(len(exam.posts(res)), 1)
        off = dict(cfg, seawatch=dict(cfg["seawatch"], early_alerts=dict(cfg["seawatch"]["early_alerts"], enabled=False)))
        res = exam.decide(events(inc, early=[early]), BRAIN, SEAWATCH, off)
        self.assertEqual([p["id"] for p in exam.posts(res)], ["f"])

    def test_a_first_sighting_sent_after_the_incident_post_is_a_second_post(self):
        inc = incident("g", [spot("2026-11-27T15:30:00Z"), spot("2026-11-27T15:40:00Z")])
        early = {"id": "candidate-g", "first": "2026-11-27T20:00:00Z", "lat": 26.28, "lon": 56.25,
                 "sent_at": "2026-11-27T20:07:44Z", "promoted_to": "g"}
        cfg = dict(CFG, seawatch=dict(CFG["seawatch"], enabled=True, alerts=True,
                                      early_alerts=dict(CFG["seawatch"].get("early_alerts") or {}, enabled=True)))
        res = exam.decide(events(inc, early=[early]), BRAIN, SEAWATCH, cfg)
        self.assertEqual(len(exam.posts(res)), 2)
        self.assertEqual(exam.score(res, [])["sea_posts_maybe_same_fire"], 1)


class Score(unittest.TestCase):
    def test_the_same_fire_at_sea_is_a_guess_by_drift(self):
        a = {"t": "2026-11-27T15:52:00Z", "lat": 26.269, "lon": 56.270}
        self.assertTrue(exam.same_fire_at_sea(a, {"t": "2026-11-27T20:07:00Z", "lat": 26.280, "lon": 56.248}))
        self.assertFalse(exam.same_fire_at_sea(a, {"t": "2026-11-27T16:30:00Z", "lat": 26.6, "lon": 56.8}))
        self.assertFalse(exam.same_fire_at_sea(a, {"t": "2026-11-29T16:00:00Z", "lat": 26.269, "lon": 56.270}))

    def test_cases_are_judged_by_place_and_time(self):
        P = [{"id": "x", "t": "2026-11-26T23:00:00Z", "lat": 26.28, "lon": 56.25, "place": "sea", "early": False}]
        case = {"id": "k", "where": [26.29, 56.27, 8], "from": "2026-11-26T18:00:00Z",
                "to": "2026-11-27T06:00:00Z", "truth": "post", "post_by": "2026-11-26T23:30:00Z"}
        res = {"incidents": {}, "early": []}
        self.assertTrue(exam.judge_case(case, res, P)["ok"])
        self.assertFalse(exam.judge_case(dict(case, post_by="2026-11-26T22:30:00Z"), res, P)["ok"])
        self.assertFalse(exam.judge_case(dict(case, truth="no_post"), res, P)["ok"])
        self.assertIsNone(exam.judge_case(dict(case, truth="open"), res, P)["ok"])
        self.assertTrue(exam.judge_case(dict(case, truth="one_post"), res, P)["ok"])


class KeptEvents(unittest.TestCase):
    def test_the_kept_events_load_and_replay(self):
        ev = exam.load_events()
        if not ev["incidents"]:
            self.skipTest("no kept events")
        result = exam.decide(ev, BRAIN, SEAWATCH, CFG)
        card = exam.score(result, exam.load_cases())
        self.assertEqual(card["incidents"], len(ev["incidents"]))
        self.assertGreater(card["posts"], 0)


if __name__ == "__main__":
    unittest.main()
