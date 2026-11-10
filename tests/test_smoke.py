"""Smoke as a second witness, silent for now (rasad/smoke.py): a plume counts only when it is new,
anchored at the heat, runs one way in several pictures and is not cold. Made-up scenes; no network."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from rasad import engine, smoke
from rasad.brain import Memory
from rasad.util import load_json, parse_time, save_json

N, KM, C = 160, 0.5, 79.5          # an 80 km box at 500 m; the heat is at its centre
T = parse_time


def scene(seed=0, level=40.0, noise=1.0, coast=True):
    """Sea (dark) with a strip of land (bright) on the right, and a little picture noise."""
    rng = np.random.default_rng(seed)
    a = np.full((N, N), level, np.float32)
    if coast:
        a[:, 125:] = level + 60
    return a + rng.normal(0, noise, (N, N)).astype(np.float32)


def streak(a, length_km, bearing=45.0, start=(C, C), strength=14.0, width_px=1.5):
    """Add a plume that starts at ``start`` (y, x) and runs ``length_km`` towards ``bearing``."""
    yy, xx = np.mgrid[:N, :N].astype(np.float32)
    ux, uy = np.sin(np.radians(bearing)), -np.cos(np.radians(bearing))
    along = (xx - start[1]) * ux + (yy - start[0]) * uy
    across = -(xx - start[1]) * uy + (yy - start[0]) * ux
    inside = (along >= 0) & (along <= length_km / KM) & (np.abs(across) <= width_px * (1 + along / 30))
    return a + np.where(inside, strength, 0).astype(np.float32)


def flat_ir(seed=100):
    return np.full((N, N), 120, np.float32) + np.random.default_rng(seed).normal(0, 1.0, (N, N)).astype(np.float32)


class PlumeTests(unittest.TestCase):
    def setUp(self):
        self.refs = [scene(1), scene(2), scene(3)]
        self.refs_ir = [flat_ir(101), flat_ir(102), flat_ir(103)]
        self.ir = [flat_ir(110 + i) for i in range(5)]

    def find(self, pictures, ir=None, refs=None):
        return smoke.find_plume(pictures, ir or self.ir[:len(pictures)], refs or self.refs, self.refs_ir, C, C, KM)

    def test_a_growing_plume_from_the_heat_is_seen_with_its_length_and_direction(self):
        pictures = [streak(scene(10 + i), 6 + 3 * i) for i in range(5)]           # 6 km growing to 18 km, towards NE
        found = self.find(pictures)
        self.assertTrue(found["seen"])
        self.assertEqual(found["frames"], 5)
        self.assertAlmostEqual(found["length_km"], 18, delta=2.5)
        self.assertAlmostEqual(found["bearing"], 45, delta=8)
        self.assertTrue(found["brighter"])
        self.assertLess(found["ir_z"], 1.0)
        self.assertIn("دخان محتمل من موضع الحرارة (تجريبي)", smoke.text(dict(found, direction="شمال شرق")))
        self.assertEqual(smoke.text({"seen": False}), "")

    def test_black_smoke_over_bright_ground_is_seen_as_a_darker_streak(self):
        refs = [scene(1, level=150, coast=False), scene(2, level=150, coast=False)]
        pictures = [streak(scene(20 + i, level=150, coast=False), 14, bearing=200, strength=-16) for i in range(4)]
        found = self.find(pictures, refs=refs)
        self.assertTrue(found["seen"])
        self.assertFalse(found["brighter"])
        self.assertAlmostEqual(found["bearing"], 200, delta=8)

    def test_a_brighter_day_or_haze_over_the_whole_scene_is_not_smoke(self):
        pictures = [scene(30 + i) * 1.25 + 8 for i in range(5)]                    # the sun climbed
        found = self.find(pictures)
        self.assertFalse(found["seen"])
        self.assertEqual(found["frames"], 0)

    def test_a_streak_that_drifts_is_a_cloud(self):
        # the same streak, 4 km further east in every picture: only one picture has it at the heat
        pictures = [streak(scene(40 + i), 12, start=(C, C - 16 + 8 * i)) for i in range(5)]
        found = self.find(pictures)
        self.assertFalse(found["seen"])
        self.assertLessEqual(found["frames"], 2)

    def test_a_streak_that_was_already_there_is_not_new(self):
        refs = [streak(scene(1), 12), streak(scene(2), 12)]
        pictures = [streak(scene(50 + i), 12) for i in range(5)]
        self.assertFalse(self.find(pictures, refs=refs)["seen"])

    def test_a_streak_that_is_cold_in_the_infrared_is_a_cloud(self):
        pictures = [streak(scene(60 + i), 12) for i in range(5)]
        cold = [streak(flat_ir(120 + i), 12, strength=-25) for i in range(5)]
        found = self.find(pictures, ir=cold)
        self.assertFalse(found["seen"])
        self.assertIn("cloud", found["why"])

    def test_a_round_patch_or_a_streak_passing_through_the_heat_is_not_a_plume(self):
        yy, xx = np.mgrid[:N, :N]
        blob = [scene(70 + i) + np.where((yy - C) ** 2 + (xx - C) ** 2 <= 36, 15, 0) for i in range(5)]
        self.assertFalse(self.find(blob)["seen"])
        through = [streak(scene(80 + i), 16, start=(C + 11, C - 11)) for i in range(5)]   # the heat is in its middle
        self.assertFalse(self.find(through)["seen"])
        elsewhere = [streak(scene(90 + i), 12, start=(C - 30, C - 40)) for i in range(5)]  # 25 km from the heat
        self.assertFalse(self.find(elsewhere)["seen"])

    def test_a_sky_filling_with_new_cloud_gives_no_answer(self):
        rng = np.random.default_rng(5)
        pictures = []
        for i in range(5):
            a = streak(scene(95 + i), 12)
            for _ in range(60):
                y, x = rng.integers(5, N - 12, 2)
                a[y:y + 7, x:x + 7] += 20
            pictures.append(a)
        found = self.find(pictures)
        self.assertFalse(found["seen"])
        self.assertIn("cloud", found["why"])

    def test_two_pictures_are_not_enough_and_missing_pictures_are_not_evidence(self):
        pictures = [scene(11), scene(12), None, streak(scene(13), 10), streak(scene(14), 12)]
        found = self.find(pictures, ir=self.ir)
        self.assertFalse(found["seen"])
        self.assertEqual((found["frames"], found["checked"]), (2, 4))
        self.assertFalse(smoke.find_plume([streak(scene(15), 12)] * 4, None, [], [], C, C, KM)["seen"], "no scene before the fire")


class RealCaseTests(unittest.TestCase):
    """The ship fire reported near Hormuz on the morning of 10 Nov 2026 (docs/reviews): the real
    visible and infrared pictures, 06:50 to 08:30 UTC, 80 km around 26.40 N 56.93 E."""

    def setUp(self):
        d = np.load(Path(__file__).parent / "fixtures" / "smoke_hormuz_20261110.npz")
        self.vis = [a.astype(np.float32) for a in d["vis"]]
        self.ir = [a.astype(np.float32) for a in d["ir"]]
        self.times = [str(t)[11:] for t in d["times"]]

    def test_a_streak_shows_in_one_picture_only_so_it_is_not_called_smoke(self):
        refs, refs_ir = self.vis[:3], self.ir[:3]                          # 06:50 to 07:10: before anything showed
        P = smoke.DEFAULTS
        ref, ref_ir = np.median(np.stack(refs), axis=0), np.median(np.stack(refs_ir), axis=0)
        streaks = {}
        for i in range(3, len(self.vis)):
            shape, _ = smoke.plume_in_picture(self.vis[i], ref, self.ir[i], ref_ir, C, C, KM, P)
            if shape:
                streaks[self.times[i]] = shape
        self.assertEqual(list(streaks), ["08:00"], "a bright streak from that place, at 08:00 only")
        at_eight = streaks["08:00"]
        self.assertAlmostEqual(at_eight["length_km"], 15, delta=3)
        self.assertLess(at_eight["ir_z"], 1.0, "not cold: no cloud top")
        for end in range(5, len(self.vis)):
            found = smoke.find_plume(self.vis[max(3, end - 4):end + 1], self.ir[max(3, end - 4):end + 1], refs, refs_ir, C, C, KM)
            self.assertFalse(found["seen"], self.times[end])
            self.assertLessEqual(found["frames"], 1)
        # the same pictures with a single picture allowed would have called it smoke: the rule matters
        loose = smoke.find_plume(self.vis[5:8], self.ir[5:8], refs, refs_ir, C, C, KM, {"min_frames": 1})
        self.assertTrue(loose["seen"])


class FakePictures:
    """Stands in for seawatch.Pictures: a plume grows from 08:00 on; the infrared stays flat."""

    def __init__(self, plume=True):
        self.plume, self.asked = plume, []

    def get(self, layer, bbox, W, H, t, channel=None):
        self.asked.append((layer, t))
        minutes = int((t - T("2026-11-10T08:00:00Z")).total_seconds() // 60)
        seed = abs(minutes) + (500 if "ir" in layer else 0)
        if "ir" in layer:
            return flat_ir(seed)
        a = scene(seed)
        return streak(a, 6 + minutes / 4) if self.plume and minutes >= 0 else a


class SilentRecordingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.now = T("2026-11-10T08:42:00Z")
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
        self.cfg["regions"] = [{"id": "gulf", "name": "الخليج الفارسي", "bbox": [47.8, 23.5, 56.6, 30.5]}]
        self.cfg["seawatch"].update(enabled=False, look_back=False)
        self.cfg["eumetview"]["fire_layers"] = []
        self.cfg["clip"]["enabled"] = False
        self.cfg["satpic"]["enabled"] = False
        self.incident = {
            "id": "20261110-0800_26.000_50.000", "kind": "new", "first": "2026-11-10T08:00:00Z", "last": "2026-11-10T08:30:00Z",
            "lat": 26.0, "lon": 50.0, "best": "geo", "families": ["mtg"], "srcs": ["mtg_frp"], "geo_slots": ["mtg|2026-11-10T08:00:00Z"],
            "polar": [], "max_frp": 30.0, "frp_series": [["2026-11-10T08:00:00Z", 30.0, "mtg"]], "n": 1, "status": "new",
            "verdict": "يحتاج تأكيداً", "known_days": 0, "area_days": 0, "place": "sea", "country": "", "facility": None,
            "headline": "حريق في البحر", "where": "في البحر", "priority": "low", "_pos": [26.0, 50.0, "offline-map"]}

    def test_which_fires_are_looked_at(self):
        inc, now = self.incident, self.now
        self.assertTrue(smoke.wanted(inc, self.cfg, now))
        self.assertFalse(smoke.wanted(inc, self.cfg, T("2026-11-10T10:30:00Z")), "two hours after the last sighting: too late")
        self.assertFalse(smoke.wanted(dict(inc, last="2026-11-10T20:00:00Z"), self.cfg, T("2026-11-10T20:10:00Z")), "night")
        far_small = dict(inc, place="land", max_frp=4.0, facility={"km": 40})
        self.assertFalse(smoke.wanted(far_small, self.cfg, now), "a small fire far from anything")
        self.assertTrue(smoke.wanted(dict(far_small, facility={"km": 3}), self.cfg, now))
        self.assertTrue(smoke.wanted(dict(far_small, status="alerted"), self.cfg, now))
        self.assertFalse(smoke.wanted(dict(inc, smoke={"seen": True}), self.cfg, now), "already seen: settled")
        self.assertFalse(smoke.wanted(dict(inc, recovered_historical=True), self.cfg, now))
        self.cfg["smoke"]["enabled"] = False
        self.assertFalse(smoke.wanted(inc, self.cfg, now))

    def test_the_worker_checks_a_daytime_fire_and_the_finding_reaches_the_incident_only(self):
        smoke.request(self.cfg, self.incident, self.now)
        pics = FakePictures()
        self.assertEqual(smoke.work_one(self.cfg, self.now, pics), self.incident["id"])
        times = sorted({t for layer, t in pics.asked})
        self.assertEqual(times[0], T("2026-11-10T07:20:00Z"), "the scene 40 minutes before the heat")
        self.assertEqual(times[-1], T("2026-11-10T08:20:00Z"), "up to the newest picture already published (08:42 now)")
        self.assertTrue(smoke.collect(self.cfg, self.incident, self.now))
        found = self.incident["smoke"]
        self.assertTrue(found["seen"])
        self.assertEqual(found["at"], "2026-11-10T08:20:00Z")
        self.assertEqual(found["direction"], "شمال شرق")
        self.assertGreaterEqual(found["frames"], 3)
        self.assertIsNone(smoke.work_one(self.cfg, self.now + dt.timedelta(minutes=10), FakePictures()), "seen once: not checked again")
        self.assertFalse(smoke.collect(self.cfg, self.incident, self.now), "nothing new to copy")

    def test_no_plume_is_checked_again_with_the_next_picture_and_then_dropped(self):
        smoke.request(self.cfg, self.incident, self.now)
        self.assertIsNotNone(smoke.work_one(self.cfg, self.now, FakePictures(plume=False)))
        smoke.collect(self.cfg, self.incident, self.now)
        self.assertFalse(self.incident["smoke"]["seen"])
        self.assertIsNone(smoke.work_one(self.cfg, self.now + dt.timedelta(minutes=4), FakePictures(plume=False)))
        self.assertIsNotNone(smoke.work_one(self.cfg, self.now + dt.timedelta(minutes=11), FakePictures(plume=False)))
        late = T("2026-11-10T10:30:00Z")
        self.assertIsNone(smoke.work_one(self.cfg, late, FakePictures(plume=False)))
        self.assertEqual(list((smoke.queue_dir(self.cfg) / "jobs").glob("*.json")), [], "the job is forgotten when it is too late")

    def test_at_night_nothing_is_fetched(self):
        pics = FakePictures()
        found = smoke.check(self.cfg, pics, 26.0, 50.0, T("2026-11-10T19:00:00Z"), T("2026-11-10T19:40:00Z"))
        self.assertEqual((found["seen"], found["why"], pics.asked), (False, "not in fair daylight", []))

    def test_a_whole_check_records_the_smoke_without_posting_or_changing_the_verdict(self):
        save_json(self.cfg["_state_dir"] / "state.json", {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE,
                                                          "sea_check": engine.SEA_CHECK})
        memory = Memory({})
        memory.record(30, 48, "2026-11-07", 10)
        save_json(self.cfg["_state_dir"] / "memory.json", memory.cells)
        save_json(self.cfg["_state_dir"] / "incidents.json", [self.incident])
        session = Mock()
        for method in ("get", "post", "put"):
            getattr(session, method).side_effect = AssertionError("No network in a check")
        geo = Mock(version="offline-map")
        geo.place_kind.return_value = "sea"
        geo.facility.return_value = None
        geo.describe.return_value = ("في البحر", "")
        deliver = Mock(side_effect=AssertionError("the smoke check never posts"))
        with patch.multiple(engine, utcnow=Mock(side_effect=lambda: self.now), make_session=Mock(return_value=session),
                            WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
                            latest_time=Mock(side_effect=AssertionError("No source requests")), deliver=deliver):
            state = engine.run_cycle(self.cfg, send=True)                      # asks the worker
            self.assertNotIn("smoke", state.incidents[0])
            smoke.work_one(self.cfg, self.now, FakePictures())
            state = engine.run_cycle(self.cfg, send=True)                      # copies the finding
        inc = state.incidents[0]
        self.assertTrue(inc["smoke"]["seen"])
        self.assertEqual((inc["verdict"], inc["status"]), ("يحتاج تأكيداً", "new"), "silent: the verdict is untouched")
        shown = load_json(self.cfg["_site_dir"] / "data" / "incidents.json", {})["items"][0]
        self.assertIn("دخان محتمل من موضع الحرارة (تجريبي)", shown["smoke_text"])
        self.assertIn("شمال شرق", shown["smoke_text"])


if __name__ == "__main__":
    unittest.main()
