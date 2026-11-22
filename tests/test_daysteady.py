"""Day sea fires that stay put (daysteady.py): shown in the app with the day finder's candidates,
never posted alone. The case: 20 Nov 2026, 24.48, 58.16, hot in every picture 09:40-11:20 UTC."""
import copy
import datetime as dt
import unittest

from rasad import brain, daysteady, engine
from rasad.util import iso

T0 = dt.datetime(2026, 11, 20, 9, 40, tzinfo=dt.timezone.utc)
CASE = [59.8, 57.9, 56.5, 54.9, 62.4, 60.3]


class _Geo:
    def __init__(self, platform=False):
        self.platform = platform

    def platform_within(self, lat, lon, km):
        return self.platform


def replay(pictures, geo=None, cfg=None):
    """pictures: one list of [lat, lon, above] per picture, ten minutes apart."""
    cfg = cfg or copy.deepcopy(engine.DEFAULTS)
    state, rows = {}, []
    for k, spots in enumerate(pictures):
        rows += daysteady.observe(cfg, state, "hormuz_oman", T0 + dt.timedelta(minutes=10 * k), spots, geo)
    return rows, state


class Steady(unittest.TestCase):
    def test_the_muscat_case_is_shown_from_its_fourth_picture(self):
        pictures = [[[24.48, 58.16, a], [24.0 + 0.1 * k, 58.9, 45.0]] for k, a in enumerate(CASE)]
        rows, _ = replay(pictures[:3], _Geo())
        self.assertEqual(rows, [])                    # three pictures: not yet
        rows, _ = replay(pictures, _Geo())
        self.assertEqual(sorted({r["t"] for r in rows}), [iso(T0 + dt.timedelta(minutes=10 * k)) for k in range(6)])
        self.assertTrue(all(abs(r["lat"] - 24.48) < 0.01 for r in rows))   # the drifting glint is not
        self.assertEqual(len(rows), 6)                # each picture once, not again in later checks

    def test_moving_weak_or_usual_heat_is_not_shown(self):
        moving = [[[24.48 + 0.03 * k, 58.16, 60.0]] for k in range(6)]   # ~3 km per picture
        self.assertEqual(replay(moving, _Geo())[0], [])
        weak = [[[24.48, 58.16, 44.0]] for _ in range(6)]                 # steady but faint
        self.assertEqual(replay(weak, _Geo())[0], [])
        gap = [[[24.48, 58.16, 60.0]], [[24.48, 58.16, 60.0]], [], [[24.48, 58.16, 60.0]], [[24.48, 58.16, 60.0]]]
        self.assertEqual(replay(gap, _Geo())[0], [])                      # not four in a row
        platform = [[[24.48, 58.16, 60.0]] for _ in range(6)]
        self.assertEqual(replay(platform, _Geo(platform=True))[0], [])

    def test_warm_land_and_a_place_steady_on_earlier_days_are_left_out(self):
        warm = [[[24.48, 58.16, 60.0, 8.0]] for _ in range(6)]           # warm at 10.5 micron too: sand in the sun
        self.assertEqual(replay(warm, _Geo())[0], [])
        cfg, state = copy.deepcopy(engine.DEFAULTS), {}
        for day in range(3):                                                # a flare the maps do not know
            t0 = T0 + dt.timedelta(days=day)
            rows = []
            for k in range(5):
                rows += daysteady.observe(cfg, state, "gulf_south", t0 + dt.timedelta(minutes=10 * k), [[24.41, 52.10, 55.0, -2.0]], _Geo())
            self.assertEqual(bool(rows), day < 2, f"day {day + 1}")

    def test_memory_stays_small(self):
        pictures = [[[24.48, 58.16, 60.0]] for _ in range(40)]
        _, state = replay(pictures, _Geo())
        rec = state["sea_day_steady"]["hormuz_oman"]
        self.assertLessEqual(len(rec["read"]), 19)    # three hours of pictures
        self.assertLessEqual(len(rec["told"]), 19)

    def test_a_steady_day_spot_alone_never_posts(self):
        inc = {"srcs": ["mtg_day_spot"], "observations": []}
        self.assertTrue(brain.day_requires_confirmation(inc))


if __name__ == "__main__":
    unittest.main()
