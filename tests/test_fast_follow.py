"""The video follows its post within seconds, and a new night picture of the priority seas is read
as soon as it is out (22 Nov 2026: a ship fire off Oman, picture 23:50, post 23:59:26, video made
at 23:59:45 but sent at 00:01:56 with the next check)."""
import copy
import datetime as dt
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from rasad import engine, mediaqueue, seawatch

TOKEN = "a" * 64
OTHER = "b" * 64


class _S:
    def __init__(self, incidents, state):
        self.incidents, self.state = incidents, state


class AwaitedVideos(unittest.TestCase):
    def test_posts_waiting_for_their_video_are_listed_and_nothing_else(self):
        S = _S(
            [{"id": "sent", "status": "alerted", "clip_video_pending": True, "media_job": TOKEN},
             {"id": "app-only", "status": "new", "clip_video_pending": True, "media_job": OTHER},
             {"id": "look", "status": "pending", "closer_look_asked": "2026-11-22T00:00:00Z", "media_job": "c" * 64},
             {"id": "had-its-video", "status": "alerted", "clip_video_pending": False, "media_job": "d" * 64}],
            {"early_night": {
                "first": {"sent_at": "2026-11-21T23:59:26Z", "video_pending": True,
                          "media_snapshot": {"media_job": "e" * 64}},
                "unsent": {"video_pending": True, "media_snapshot": {"media_job": "f" * 64}},
                "done": {"sent_at": "x", "video_pending": False, "media_snapshot": {"media_job": "0" * 64}}}})
        self.assertEqual(sorted(engine.awaited_videos(S)), [TOKEN, "c" * 64, "e" * 64])

    def test_the_list_is_written_whole_and_only_real_job_names(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = {"_state_dir": str(Path(folder) / "_state")}
            self.assertEqual(mediaqueue.write_awaited(cfg, [TOKEN, "../etc", TOKEN, None]), [TOKEN])
            self.assertEqual((Path(folder) / ".rasad-media" / "awaited").read_text(), TOKEN + "\n")


class _WMS:
    def __init__(self, newest):
        self.newest, self.asked = newest, []

    def getmap(self, layer, bbox, w, h, t=None, quiet=False, timeout=None):
        self.asked.append(t)
        return object(), min(t, self.newest)    # a time it does not have: the nearest older one


class NewPictureOut(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(engine.DEFAULTS)
        self.cfg["seawatch"]["enabled"] = True
        self.t = dt.datetime(2026, 11, 21, 23, 50, tzinfo=dt.timezone.utc)   # night over the Gulf

    def test_an_unread_picture_that_is_out_starts_the_check(self):
        wms = _WMS(self.t)
        now = self.t + dt.timedelta(minutes=7)
        self.assertEqual(seawatch.new_picture_out(self.cfg, wms, {"seawatch": {}}, now), self.t)

    def test_not_out_yet_read_already_or_too_early_to_ask(self):
        now = self.t + dt.timedelta(minutes=7)
        older = self.t - dt.timedelta(minutes=10)
        read = {f"{b}|{seawatch.iso(older)}": "x" for b in self.cfg["seawatch"]["priority_boxes"]}
        wms = _WMS(older)
        self.assertIsNone(seawatch.new_picture_out(self.cfg, wms, {"seawatch": read}, now))
        self.assertEqual(wms.asked, [self.t])            # the older one was read: not asked again
        wms = _WMS(self.t)
        self.assertIsNone(seawatch.new_picture_out(self.cfg, wms, {"seawatch": read},
                                                   self.t + dt.timedelta(minutes=2)))
        self.assertEqual(wms.asked, [])                  # two minutes after its time: never out yet

    def test_by_day_it_is_not_asked(self):
        noon = dt.datetime(2026, 11, 22, 9, 0, tzinfo=dt.timezone.utc)
        wms = _WMS(noon)
        self.assertIsNone(seawatch.new_picture_out(self.cfg, wms, {}, noon + dt.timedelta(minutes=7)))
        self.assertEqual(wms.asked, [])


class LoopWaitsLess(unittest.TestCase):
    def test_a_made_video_or_a_new_picture_ends_the_wait_once_each(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "loop.sh"
        text = script.read_text()
        # the shell helper, run on its own against a made-up queue
        start = text.index("declare -A hurried=()")
        helper = text[start:text.index("while [ \"$SECONDS\" -lt \"$end\" ]")]
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / ".rasad-media"
            (media / TOKEN).mkdir(parents=True)
            (media / "awaited").write_text(TOKEN + "\n../x\n" + OTHER + "\n")
            run = helper + """
video_ready && echo first
video_ready && echo again
mkdir -p .rasad-media/%s .rasad-media/%s && touch .rasad-media/%s/result.json
video_ready && echo none-yet
touch .rasad-media/%s/result.json
video_ready && echo made
video_ready && echo once
true
""" % (OTHER, "c" * 64, "c" * 64, TOKEN)
            out = subprocess.run(["bash", "-c", run], cwd=folder, capture_output=True, text=True, check=True).stdout
            self.assertEqual(out.split(), ["made"])
            self.assertIn("sea-ready", text)
            self.assertIn('hurried[$slot]=1', text)


if __name__ == "__main__":
    unittest.main()


class VideoCloseUp(unittest.TestCase):
    """The owner, 22 Nov 2026: the video must be close on the fire so that it shows big. The
    pictures are still read and judged over 40 km; the video shows the middle, twice as near."""

    def hot_width(self, zoom):
        import numpy as np
        from PIL import Image
        from rasad import twopanel
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["clip"]["view_zoom"] = zoom
        t_end = dt.datetime(2026, 11, 21, 23, 50, tzinfo=dt.timezone.utc)

        class W:
            def getmap(self, name, bbox, w, h, t=None, style="", quiet=True, timeout=None):
                a = np.full((h, w, 4), 60, np.uint8)
                a[..., 3] = 255
                if t >= t_end - dt.timedelta(minutes=10):
                    yy, xx = np.mgrid[0:h, 0:w]
                    d = np.hypot(yy - h / 2 - 3, xx - w / 2 + 4)
                    a[..., 0] = np.maximum(a[..., 0], (200 * np.exp(-(d / 5) ** 2)).astype(np.uint8))
                return Image.fromarray(a), t

        with tempfile.TemporaryDirectory() as folder:
            cfg["_site_dir"] = folder
            layer = {"name": "x", "cadence_min": 10, "channel": 0, "hot_is_bright": True}
            info = {"first": "2026-11-21T23:40:00Z", "place": "sea", "where": "في البحر", "verdict": "يحتاج تأكيداً"}
            _, snap = twopanel.render(cfg, W(), 23.778, 57.96, t_end, os.path.join(folder, "clips", "t"),
                                      layer, None, 8, info)
            img = np.asarray(Image.open(snap).convert("RGB")).astype(int)
        left = img[140:930, 12:954]                       # the heat picture
        hot = (left[..., 0] > 235) & (left[..., 1] > 150) & (left[..., 2] < 200)  # the fire's bright core
        cols = np.nonzero(hot.any(axis=0))[0]
        rows = np.nonzero(hot.any(axis=1))[0]
        centre = ((cols.min() + cols.max()) / 2, (rows.min() + rows.max()) / 2)
        return cols.max() - cols.min() + 1, centre

    def test_the_fire_shows_twice_as_big_in_the_middle(self):
        near, (cx, cy) = self.hot_width(2)
        far, _ = self.hot_width(1)
        self.assertGreaterEqual(near, 1.7 * far)
        self.assertLess(abs(cx - 471), 40)               # in the middle of the heat picture
        self.assertLess(abs(cy - 394), 40)
        self.assertEqual(engine.DEFAULTS["clip"]["view_zoom"], 2)


class SeaIsNotAFlare(unittest.TestCase):
    def test_a_strait_where_ships_burned_on_a_few_days_is_not_usual_heat(self):
        self.assertFalse(engine.usual_heat(True, False, True), "Hormuz, 23 Nov 2026: 6 days of 30, open water")
        self.assertTrue(engine.usual_heat(True, True, True), "heat on most days is usual even in open water")
        self.assertTrue(engine.usual_heat(True, False, False), "on land, at a platform or in a platform field "
                        "the rule is as before: Zakum, 10 days of 30, is its usual flare")
        self.assertFalse(engine.usual_heat(False, False, True))


class DriftingShip(unittest.TestCase):
    """Hormuz, 23-24 Nov 2026: one burning ship drifted 10 km north-west in 5.5 hours."""

    def incident(self):
        track = [("2026-11-23T20:10:00Z", 26.3072, 56.2852), ("2026-11-23T22:00:00Z", 26.3453, 56.2975),
                 ("2026-11-24T00:00:00Z", 26.3656, 56.2728), ("2026-11-24T00:30:00Z", 26.3813, 56.2530)]
        return {"id": "ship", "lat": 26.349, "lon": 56.287, "place": "sea", "first": track[0][0], "last": track[-1][0],
                "observations": [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": la, "lon": lo}
                                 for t, la, lo in track]}

    def test_a_drifting_fire_stays_one_fire(self):
        from rasad import brain
        inc = self.incident()
        det = {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": "2026-11-24T01:10:00Z", "lat": 26.3925, "lon": 56.2480}
        self.assertGreater(brain.haversine_km(det["lat"], det["lon"], inc["lat"], inc["lon"]), 6)
        self.assertIs(brain.find_incident([inc], det, 6, 12), inc, "it continues the track")
        far = dict(det, lat=26.45, lon=56.20)                  # 7 km from the last sighting: another fire
        self.assertIsNone(brain.find_incident([inc], far, 6, 12))
        late = dict(det, t="2026-11-24T03:00:00Z")             # the track went cold two and a half hours ago
        self.assertIsNone(brain.find_incident([inc], late, 6, 12))
        land = dict(inc, place="land")                          # on land a fire does not move
        self.assertIsNone(brain.find_incident([land], det, 6, 12))


class TwoShips(unittest.TestCase):
    """Two ships hit near each other within the hour are two fires; one drifting ship stays one."""

    def test_two_spots_of_one_picture_a_few_km_apart_are_two_fires(self):
        from rasad import brain
        spot = lambda t, la, lo: {"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": t, "lat": la, "lon": lo}
        first = spot("2026-11-24T01:00:00Z", 26.390, 56.250)
        a = {"id": "a", "lat": 26.390, "lon": 56.250, "place": "sea", "first": first["t"], "last": first["t"],
             "observations": [dict(first)]}
        other = spot("2026-11-24T01:00:00Z", 26.390, 56.285)       # 3.5 km east, the same picture
        self.assertIsNone(brain.find_incident([a], other, 6, 12), "a second ship, not the first one")
        b = {"id": "b", "lat": 26.390, "lon": 56.285, "place": "sea", "first": other["t"], "last": other["t"],
             "observations": [dict(other)]}
        # the next picture: each ship's spot goes to its own fire
        self.assertIs(brain.find_incident([a, b], spot("2026-11-24T01:10:00Z", 26.391, 56.251), 6, 12), a)
        self.assertIs(brain.find_incident([a, b], spot("2026-11-24T01:10:00Z", 26.391, 56.284), 6, 12), b)
        # the same spot given again (its strength read later) is the same fire, not another
        self.assertIs(brain.find_incident([a], dict(first), 6, 12), a)


class EarlierFireInTheFirstPost(unittest.TestCase):
    """Hormuz, 25 Nov 2026: NASA's day pass found a ship fire 14 km from the confirmed fire of the
    night before; the post at 13:02 did not say so, because a new record learnt that it was at sea
    only after the earlier fire had been looked for."""

    def test_a_new_record_at_sea_says_the_confirmed_fire_close_by(self):
        from unittest.mock import Mock, patch
        from rasad import brain
        from rasad.util import UTC, iso, save_json
        now = dt.datetime(2026, 11, 25, 10, 30, tzinfo=UTC)
        night = [{"src": "mtg_spot", "family": "mtg", "kind": "geo", "t": f"2026-11-25T00:{m:02d}:00Z",
                  "lat": 26.3566, "lon": 56.248} for m in (0, 10, 20, 30)]
        night.append({"src": "VIIRS_NOAA21_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-24T22:49:00Z",
                      "lat": 26.36, "lon": 56.25, "frp": 3.4, "conf": "n", "night": True})
        old = brain.new_incident(night[-1], "new")
        for d in night:
            brain.add_to_incident(old, d)
        old.update(place="sea", where="في البحر قرب مضيق هرمز", country="", regions=["hormuz"],
                   region_name="مضيق هرمز", facility=None, known_days=0, area_days=0, typical_frp=None)
        cfg = copy.deepcopy(engine.DEFAULTS)
        brain.finalize(old, cfg)
        self.assertEqual(old["verdict"], "مؤكد")
        old.update(status="alerted", alerted_verdict="مؤكد", alerted_at="2026-11-24T23:40:00Z", tg_msg=1)
        row = {"src": "VIIRS_NOAA20_NRT", "family": "viirs", "kind": "polar", "t": "2026-11-25T10:02:00Z",
               "lat": 26.2893, "lon": 56.1398, "frp": 8.9, "conf": "n", "night": False, "satellite": "N20",
               "instrument": "VIIRS"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg.update(_state_dir=root / "state", _site_dir=root / "site", _geodata_dir=root / "geo")
            cfg["regions"] = [{"id": "hormuz", "name": "مضيق هرمز", "bbox": [55.4, 25.3, 57.6, 27.3]}]
            cfg["seawatch"].update(enabled=False, look_back=False)
            cfg["eumetview"]["fire_layers"] = []
            cfg["clip"]["enabled"] = False
            cfg["firms"]["map_key"] = "offline-test-key"
            cfg["noaa"]["enabled"] = True
            save_json(root / "state" / "state.json",
                      {"schema": 3, "rules": engine.RULES, "clip_style": engine.CLIP_STYLE, "sea_check": engine.SEA_CHECK,
                       "bootstrapped": iso(now), "hello_sent": iso(now), "slots": {}})
            save_json(root / "state" / "incidents.json", [old])
            memory = brain.Memory({})                      # past the learning days, quiet at this spot
            memory.record(30, 48, "2026-11-01", 10)
            memory.record(30, 48, "2026-11-20", 10)
            save_json(root / "state" / "memory.json", memory.cells)
            session = Mock()
            session.get.side_effect = AssertionError("No network")
            geo = Mock(version="offline-map")
            geo.place_kind.return_value = "sea"
            geo.facility.return_value = {"label": "منصة نفط بحرية قرب بخا", "code": "platform", "km": 9.8}
            geo.describe.return_value = ("في البحر قرب مضيق هرمز", "")
            geo.solar_km.return_value = None
            geo.fac.nearest.return_value = (None, None)
            sent = Mock(return_value=None)
            forbidden = Mock(side_effect=AssertionError("Unexpected source"))
            with patch.multiple(engine, utcnow=Mock(return_value=now), make_session=Mock(return_value=session),
                                WMS=Mock(), Geo=Mock(return_value=geo), Availability=Mock(),
                                firms_fetch=Mock(return_value=[]), latest_time=forbidden, detect_geo_layer=forbidden,
                                deliver=sent, tg_send=forbidden), \
                    patch.object(engine.noaafire, "collect", Mock(return_value=[row])):
                S = engine.run_cycle(cfg, send=False)
        new = next(i for i in S.incidents if i["first"] == "2026-11-25T10:02:00Z")
        self.assertEqual(new["near_confirmed"]["id"], old["id"])
        self.assertEqual(new["near_posted"]["id"], old["id"])
        text = brain.alert_message(new, cfg)
        # the fire of the night before, drifting (docs/reviews/hormuz_dusk_20261125.md): where it was
        # last seen, and that it may be the same fire
        line = "📌 على بعد 13 كم من آخر موضع لحريق في البحر نُشر عند 02:40، آخر رصد له قبل 10 ساعات"
        self.assertIn(line, text)
        posted = " ".join(str(c.args[2]) for c in sent.call_args_list if len(c.args) > 2)
        self.assertIn(line, posted, "the first post itself says it")


class RightNames(unittest.TestCase):
    """Shaybah, 25 Nov 2026: the open map calls the field «Shaybah Refinery», and the post placed it
    by the nearest town, Batin Liwa, across the border in the UAE. The owner gave its right name."""

    def enriched(self, names=None):
        from unittest.mock import Mock
        from rasad import brain, geodb
        from rasad.util import save_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = copy.deepcopy(engine.DEFAULTS)
            cfg.update(_geodata_dir=root / "geo", _state_dir=root / "state", _site_dir=root / "site")
            if names:
                cfg["geodata"]["names"] = names
            save_json(root / "geo" / "facilities.json",
                      {"built": "2026-11-24", "items": [[22.67995, 54.12731, "Shaybah Refinery", "مصفاة", "refinery", ""]]})
            save_json(root / "geo" / "places.json", {"items": [[22.91, 53.96, "بطين ليوا", "village"]]})
            blank = {"country": "", "state": "", "local": ""}
            state = {"geocode": {"22.68,54.14": blank, "22.91,53.96": blank}}
            geo = geodb.Geo(cfg, Mock(), Mock(), state)
            det = {"src": "mtg_frp", "family": "mtg", "kind": "geo", "t": "2026-11-25T12:10:00Z",
                   "lat": 22.6769, "lon": 54.1375, "frp": 51.3}
            inc = brain.new_incident(det, "new")
            brain.add_to_incident(inc, det)
            inc.update(known_days=0, area_days=0, typical_frp=None)
            return engine.enrich(inc, cfg, geo), geo

    def test_the_field_is_told_by_its_right_name_and_country(self):
        before, geo_before = self.enriched()
        self.assertIn("بطين ليوا", before["where"])
        self.assertEqual(before["facility"]["label"], "مصفاة (Shaybah Refinery)")
        after, geo_after = self.enriched({"Shaybah Refinery": {"name": "حقل شيبة", "country": "السعودية"}})
        self.assertEqual(after["facility"]["label"], "حقل شيبة")
        self.assertEqual(after["where"], "في حقل شيبة (السعودية)")
        self.assertEqual(after["country"], "السعودية")
        self.assertIn("حقل شيبة", after["headline"])
        self.assertNotEqual(geo_before.version, geo_after.version, "recent incidents are described again")


class SteadyAtSea(unittest.TestCase):
    """A fire lit before dark is «hot before» to every rule that looks for new heat (Hormuz, 25 Nov
    2026). A spot clearly warmer than the clear sea in three dark pictures in a row is told too."""

    WHEN = dt.datetime(2026, 11, 25, 16, 0, tzinfo=dt.timezone.utc)

    def find(self, above=20.0, pictures=3, platform=None, floor=12.0, beside_km=None):
        from unittest.mock import Mock, patch
        import numpy as np
        from rasad import patterns
        from rasad.util import box_around, px_to_latlon
        cfg = copy.deepcopy(engine.DEFAULTS)
        cfg["seawatch"]["steady_min_above"] = floor
        box = {"id": "test", "bbox": box_around(26.30, 56.10, 25)}
        W, H = seawatch.box_size(box, cfg["seawatch"]["km_per_px"])
        r, c = H // 2, W // 2
        lat, lon = px_to_latlon(box["bbox"], W, H, c, r)
        sea = np.full((H, W), 30.0, dtype=np.float32)
        hot = sea.copy()
        hot[r - 1:r + 2, c - 1:c + 2] += above          # the same spot in every picture: «hot before»
        dark = np.zeros((H, W), dtype=np.float32)
        cad = dt.timedelta(minutes=10)
        lit = {self.WHEN - k * cad for k in range(pictures)}

        def get(layer, bbox, w, h, t, channel=None):
            if channel in (1, 2):
                return dark
            return hot if t in lit or t < self.WHEN - 2 * cad else sea
        pics = Mock()
        pics.get.side_effect = get
        open_sea = lambda lats, lons: np.zeros_like(np.asarray(lats), dtype=bool)
        real_steady = seawatch.steady_spots

        def steady(cfg_, pics_, box_, t_, cur, sea_, km, W_, H_, found, *rest):
            if beside_km is not None:   # a fire the finder found in the same picture, beside_km east
                import math
                found = found + [{"lat": lat, "lon": lon + beside_km / (111.32 * math.cos(math.radians(lat)))}]
            return real_steady(cfg_, pics_, box_, t_, cur, sea_, km, W_, H_, found, *rest)
        with patch.dict(seawatch._SEA, {}, clear=True), \
                patch.object(seawatch, "land_mask_points", return_value=open_sea), \
                patch.object(seawatch, "sun_elevation", return_value=-30.0), \
                patch.object(seawatch, "_ir105_change", return_value=0.7), \
                patch.object(seawatch, "steady_spots", steady), \
                patch.object(patterns, "platform_area", return_value=platform):
            spots, notes = seawatch.find_spots(cfg, pics, box, self.WHEN, mem=None, geo=None)
        return spots, notes, (lat, lon)

    def test_the_end_of_a_found_fires_streak_is_not_another_fire(self):
        # Hormuz, 25 Nov 2026, 20:10 Baghdad time: the west end of the streak of a fire the finder was
        # following, 4 km east of it in the same picture, was posted as a new fire
        self.assertEqual([sp for sp in self.find(beside_km=4.0)[0] if sp.get("steady")], [])
        self.assertEqual(len([sp for sp in self.find(beside_km=8.0)[0] if sp.get("steady")]), 1)

    def test_heat_lit_before_dark_is_told_from_three_dark_pictures(self):
        spots, notes, (lat, lon) = self.find()
        steady = [sp for sp in spots if sp.get("steady")]
        self.assertEqual(len(steady), 1, notes)
        self.assertEqual(len(steady[0]["steady"]), 2, "the two pictures before it are sightings too")
        self.assertFalse(steady[0]["strong"], "never a first sighting from one picture")
        self.assertAlmostEqual(steady[0]["lat"], lat, places=2)

    def test_not_in_two_pictures_not_faint_not_among_platforms_not_when_off(self):
        self.assertEqual([sp for sp in self.find(pictures=2)[0] if sp.get("steady")], [])
        self.assertEqual([sp for sp in self.find(above=8.0)[0] if sp.get("steady")], [])
        spots, notes, _ = self.find(platform={"flare_km": 2.0, "spot_days": 5})
        self.assertEqual([sp for sp in spots if sp.get("steady")], [])
        self.assertIn("steady: a field of platforms (NASA's long-term map)", notes["rejected"])
        self.assertEqual([sp for sp in self.find(floor=0)[0] if sp.get("steady")], [], "off unless set")


class FirstSightingInTheChannelsLook(unittest.TestCase):
    """Hormuz, 25 Nov 2026 (17:50 Baghdad): a first sighting's video came in the old close-up look,
    because the usual video could not make out the faint speck by itself. Now it keeps the channel's
    look, centred where the finder saw it; the closer look itself still refuses what it cannot see."""

    def render(self, trust):
        import numpy as np
        from PIL import Image
        from rasad import twopanel
        cfg = copy.deepcopy(engine.DEFAULTS)
        t_end = dt.datetime(2026, 11, 25, 14, 50, tzinfo=dt.timezone.utc)

        class W:       # the sea stays the same: nothing the usual video can make out by itself
            def getmap(self, name, bbox, w, h, t=None, style="", quiet=True, timeout=None):
                a = np.full((h, w, 4), 60, np.uint8)
                a[..., 3] = 255
                if t >= t_end:
                    a[h // 2, w // 2, 0] = 64
                return Image.fromarray(a), t

        with tempfile.TemporaryDirectory() as folder:
            cfg["_site_dir"] = folder
            layer = {"name": "x", "cadence_min": 10, "channel": 0, "hot_is_bright": True}
            info = {"first": "2026-11-25T14:50:00Z", "place": "sea", "where": "في البحر قرب مضيق هرمز",
                    "verdict": "يحتاج تأكيداً", **({"trust_center": True} if trust else {})}
            return twopanel.render(cfg, W(), 26.3161, 56.2629, t_end, os.path.join(folder, "clips", "t"),
                                   layer, None, 8, info)

    def test_the_closer_look_still_refuses_but_a_posted_sighting_keeps_the_new_look(self):
        from rasad.clip import NotVisible
        with self.assertRaises(NotVisible):
            self.render(False)
        video, snap = self.render(True)
        self.assertTrue(str(video).endswith((".mp4", ".gif")))
