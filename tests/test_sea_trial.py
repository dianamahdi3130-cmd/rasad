"""The two switchable sea rules of brain.open_sea_fire and their silent trial (28 Nov 2026).

open_water_rare: in open water, earlier heat at the spot counts only on most days (Khasab, 26 Nov:
one earlier day, the burning ship of 23-25 Nov, held NASA's 3.7 MW night pixels back).
one_pass_one_sighting: NASA pixels of one pass are one look (the 1.1 MW pair posted on 26 Nov).
Off for the channel (alerts.sea_rules); alerts.sea_trial records what they would decide, in silence.
"""
import copy
import os
import tempfile
import types
import unittest

from rasad import brain, engine

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = engine.load_config(os.path.join(HERE, "config.yaml"), state_dir=tempfile.mkdtemp(),
                         site_dir=tempfile.mkdtemp())
PORT = {"name": "ميناء خصب", "cat": "ميناء", "code": "port", "label": "ميناء خصب", "km": 8.6,
        "nearest_km": 8.6, "dir": "شمال", "lat": 26.20585, "lon": 56.24967, "text": "9 كم شمال ميناء خصب"}


def pixel(t, frp, lat, lon, src="VIIRS_NOAA20_NRT"):
    return {"src": src, "family": "viirs", "kind": "polar", "t": t, "lat": lat, "lon": lon, "frp": frp,
            "conf": "n", "night": True, "scan": 0.38, "track": 0.38}


def incident(rows, **facts):
    inc = brain.new_incident(rows[0], "new")
    inc.update({"place": "sea", "facility": None, "country": "", "regions": ["hormuz"],
                "region_name": "مضيق هرمز", "where": "في البحر", "known_days": 0, "area_days": 0,
                "typical_frp": None, "memory_days": 30})
    inc.update(facts)
    for d in rows:
        brain.add_to_incident(inc, d)
    return inc


def khasab(**facts):
    """NASA's two night pixels off Khasab, 26 Nov 2026, 22:10 UTC."""
    rows = [pixel("2026-11-26T22:10:00Z", 3.18, 26.2833, 56.2562), pixel("2026-11-26T22:10:00Z", 3.7, 26.2839, 56.2523)]
    return incident(rows, **dict({"known_days": 1, "area_days": 9, "facility": PORT}, **facts))


def gulf_pair():
    """Two 1.1 MW pixels of one night pass in the Persian Gulf, 25 Nov 2026."""
    rows = [pixel("2026-11-25T22:29:00Z", 1.1, 26.3772, 52.0673), pixel("2026-11-25T22:29:00Z", 1.1, 26.3790, 52.0700)]
    return incident(rows, region_name="الخليج الفارسي", regions=["gulf"])


def cfg_with(**alerts):
    cfg = copy.deepcopy(CFG)
    for key, value in alerts.items():
        cfg["alerts"][key] = dict(cfg["alerts"].get(key) or {}, **value)
    return cfg


class PassCount(unittest.TestCase):
    def test_pixels_of_one_pass_are_one_look(self):
        a = pixel("2026-11-25T22:29:00Z", 1.1, 26.0, 52.0)
        self.assertEqual(brain.pass_count([a, dict(a, lat=26.003)]), 1)
        self.assertEqual(brain.pass_count([a, dict(a, t="2026-11-25T22:34:00Z")]), 1)   # within ten minutes
        self.assertEqual(brain.pass_count([a, dict(a, t="2026-11-25T23:20:00Z")]), 2)
        self.assertEqual(brain.pass_count([a, dict(a, src="VIIRS_NOAA21_NRT", t="2026-11-25T23:19:00Z")]), 2)


class OpenWater(unittest.TestCase):
    def test_one_earlier_day_in_open_water_is_not_usual_heat(self):
        self.assertTrue(brain.rare_in_open_water(khasab()))

    def test_heat_on_most_days_is_usual(self):
        self.assertFalse(brain.rare_in_open_water(khasab(known_days=15)))

    def test_a_facility_close_by_is_not_open_water_but_a_port_is(self):
        plant = dict(PORT, code="oil", cat="منشأة نفط", km=3.0, nearest_km=3.0)
        self.assertFalse(brain.rare_in_open_water(khasab(facility=plant)))
        self.assertTrue(brain.rare_in_open_water(khasab(facility=dict(PORT, km=3.0, nearest_km=3.0))))


class Rules(unittest.TestCase):
    def test_khasab_waits_unless_open_water_rare(self):
        self.assertFalse(brain.open_sea_fire(khasab()))
        self.assertTrue(brain.open_sea_fire(khasab(), open_water_rare=True))
        self.assertEqual(brain.verdict_of(khasab())[0], "يحتاج تأكيداً")
        self.assertEqual(brain.verdict_of(khasab(), {"open_water_rare": True})[0], "مرجّح")

    def test_the_gulf_pair_is_one_look_with_one_pass_one_sighting(self):
        self.assertTrue(brain.open_sea_fire(gulf_pair()))
        self.assertFalse(brain.open_sea_fire(gulf_pair(), one_pass_one_sighting=True))

    def test_the_channel_keeps_the_old_rules_until_switched(self):
        self.assertEqual(brain.sea_rules(CFG), {"open_water_rare": False, "one_pass_one_sighting": False})
        inc = brain.finalize(khasab(), CFG)
        self.assertEqual(inc["verdict"], "يحتاج تأكيداً")
        inc = brain.finalize(khasab(), cfg_with(sea_rules={"open_water_rare": True}))
        self.assertEqual(inc["verdict"], "مرجّح")
        self.assertNotIn("trial", inc)          # the trial and the channel agree


class SilentTrial(unittest.TestCase):
    def test_the_trial_records_what_it_would_decide_and_changes_nothing(self):
        inc = brain.finalize(khasab(), CFG)
        self.assertEqual(inc["verdict"], "يحتاج تأكيداً")
        self.assertEqual(inc["trial"], {"verdict": "مرجّح", "live": "يحتاج تأكيداً", "rules": ["open_water_rare"]})
        pair = brain.finalize(gulf_pair(), CFG)
        self.assertEqual(pair["verdict"], "مرجّح")
        self.assertEqual(pair["trial"]["verdict"], "يحتاج تأكيداً")
        self.assertEqual(pair["trial"]["rules"], ["one_pass_one_sighting"])

    def test_the_trial_never_moves_the_channel(self):
        """Every field a post or the importance reads is the same with the trial on and off."""
        off = cfg_with(sea_trial={"enabled": False})
        for make in (khasab, gulf_pair):
            a, b = brain.finalize(make(), CFG), brain.finalize(make(), off)
            a.pop("trial", None)
            self.assertEqual(a, b)

    def test_off_means_no_trial(self):
        inc = brain.finalize(khasab(), cfg_with(sea_trial={"enabled": False}))
        self.assertNotIn("trial", inc)

    def test_land_is_never_tried(self):
        inc = brain.finalize(khasab(place="land"), CFG)
        self.assertNotIn("trial", inc)


class TrialLog(unittest.TestCase):
    def test_each_trial_verdict_is_logged_once(self):
        inc = brain.finalize(khasab(), CFG)
        S = types.SimpleNamespace(state={}, incidents=[inc])
        now = brain.parse_time("2026-11-26T23:00:00Z")
        engine.log_trials(S, now)
        engine.log_trials(S, now)
        self.assertEqual(len(S.state["trial_log"]), 1)
        row = S.state["trial_log"][0]
        self.assertEqual((row["live"], row["trial"], row["rules"], row["posted"]),
                         ("يحتاج تأكيداً", "مرجّح", ["open_water_rare"], False))


if __name__ == "__main__":
    unittest.main()
