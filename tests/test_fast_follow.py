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
        self.assertFalse(engine.usual_heat(True, False, True), "Hormuz, 23 Nov 2026: 6 days of 30, at sea")
        self.assertTrue(engine.usual_heat(True, True, True), "a platform's flare burns on most days")
        self.assertTrue(engine.usual_heat(True, False, False), "on land the rule is as before")
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
