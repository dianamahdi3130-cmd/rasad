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
