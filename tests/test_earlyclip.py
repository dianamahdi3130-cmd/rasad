"""A first accepted night spot uses its original raster, never waits for a second pass."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from rasad import earlyclip
from rasad.clip import NotVisible
from rasad.seawatch import box_size
from rasad.util import iso, latlon_to_px


class Source:
    url = "https://example.invalid/wms"

    def __init__(self, current, before, when, warning=None):
        self.current, self.before, self.when = current, before, when
        self.warning, self.calls = warning, []

    def getmap(self, layer, bbox, width, height, t=None, **kwargs):
        self.calls.append((layer, list(bbox), width, height, t))
        raw = self.current if t == self.when else self.before
        if raw is None:
            return None, None
        actual = t + self.warning if t == self.when and self.warning else None
        return Image.fromarray(raw), actual


class EarlyClipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = {"seawatch": {"layer": "mtg_fd:rgb_firetemperature", "min_rise": 6, "min_above_sea": 3},
                    "clip": {}, "local_time": {"offset_hours": 3, "name": "بغداد"}}
        self.cfg.update(_state_dir=Path(self.temp.name) / "state", _site_dir=Path(self.temp.name) / "site")
        self.when = dt.datetime(2026, 11, 13, 18, 50, tzinfo=dt.timezone.utc)
        self.now = self.when + dt.timedelta(minutes=15)
        self.box = {"id": "test", "bbox": [56.0, 26.0, 56.5, 26.5]}
        self.cfg["seawatch"].update(boxes=[self.box], km_per_px=1, ref_minutes=[30])
        self.cfg["clip"].update(format="mp4", half_size_km=15)
        width, height = box_size(self.box, 1)
        self.grid = dict(box_id="test", bbox=self.box["bbox"], width=width, height=height,
                         km_per_px=1, layer=self.cfg["seawatch"]["layer"])
        self.inc = dict(id="early-case", preliminary=True, first=iso(self.when), last=iso(self.when),
                        lat=26.25, lon=56.25, where="في البحر قرب مضيق هرمز", source_grid=self.grid)
        self.before = np.full((height, width, 4), [22, 2, 1, 255], dtype=np.uint8)
        self.current = self.before.copy()
        x, y = latlon_to_px(self.box["bbox"], width, height, self.inc["lat"], self.inc["lon"])
        self.current[round(y), round(x), :3] = [117, 41, 39]
        self.captured = []

    def fake_write(self, frames, base, fps, fmt):
        self.captured[:] = frames
        out = Path(f"{base}.{fmt}")
        out.write_bytes(b"offline renderer test stub")
        return out

    def render(self, source=None):
        source = source or Source(self.current, self.before, self.when)
        with patch.object(earlyclip, "_write", side_effect=self.fake_write):
            output = earlyclip.render(self.cfg, source, self.inc, self.now)
        return output, source

    def test_first_frame_video_uses_recorded_full_grid_and_only_past_reference(self):
        (video, snap), source = self.render()
        self.assertTrue(video.is_file())
        with Image.open(snap) as image:
            self.assertEqual(image.size, (720, 480))
        self.assertEqual(len(self.captured), 8)
        self.assertFalse(np.array_equal(np.asarray(self.captured[0]), np.asarray(self.captured[-1])))
        self.assertTrue(all(c[1:4] == (self.box["bbox"], self.grid["width"], self.grid["height"]) for c in source.calls))
        self.assertEqual({c[4] for c in source.calls}, {self.when, self.when - dt.timedelta(minutes=30)})
        self.assertTrue(all(c[4] <= self.when for c in source.calls))

    def test_video_labels_remain_provisional_and_do_not_invent_strength(self):
        self.inc.update(max_frp=53.3, rise=53.3)
        with patch.object(earlyclip, "_label", wraps=earlyclip._label) as labels:
            self.render()
        texts = [call.args[2] for call in labels.call_args_list]
        self.assertTrue(any("رصد حراري أولي" in text and "يحتاج تأكيداً" in text for text in texts))
        self.assertFalse(any("MW" in text or "53.3" in text or "مؤكد" in text for text in texts))

    def test_recorded_grid_survives_changes_to_the_current_box_and_pixel_setting(self):
        self.cfg["seawatch"].update(boxes=[{"id": "other", "bbox": [51, 22, 52, 23]}], km_per_px=.5)
        (_, _), source = self.render()
        self.assertEqual(source.calls[0][1:4], (self.box["bbox"], self.grid["width"], self.grid["height"]))

    def test_legacy_candidate_falls_back_to_enabled_detector_box(self):
        self.inc.pop("source_grid")
        self.render()

    def test_missing_or_time_substituted_current_image_cannot_generate_video(self):
        for source in (Source(None, self.before, self.when),
                       Source(self.current, self.before, self.when, warning=dt.timedelta(minutes=10))):
            with self.subTest(warning=source.warning), self.assertRaises(NotVisible):
                self.render(source)
        self.assertFalse(self.captured)

    def test_missing_transparent_or_identical_reference_cannot_generate_video(self):
        transparent = self.before.copy()
        transparent[..., 3] = 0
        for before in (None, transparent, self.current):
            with self.subTest(before=type(before)), self.assertRaises(NotVisible):
                self.render(Source(self.current, before, self.when))
        self.assertFalse(self.captured)

    def test_weak_spot_cannot_be_painted_as_a_fire(self):
        self.current[self.current[..., 0] == 117, 0] = 24
        with self.assertRaises(NotVisible):
            self.render()

    def test_future_or_invalid_provenance_is_rejected_before_network(self):
        source = Source(self.current, self.before, self.when)
        for mutation in ({"first": iso(self.now + dt.timedelta(minutes=1))},
                         {"source_grid": dict(self.grid, width=100)},
                         {"source_grid": dict(self.grid, bbox=[56, 26, 56, 27])},
                         {"source_grid": dict(self.grid, km_per_px=True)},
                         {"source_grid": dict(self.grid, layer="different-source")},
                         {"source_grid": "not geometry"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                earlyclip.render(self.cfg, source, dict(self.inc, **mutation), self.now)
        self.assertFalse(source.calls)

    def test_validated_detector_cache_can_render_without_network(self):
        self.render()
        source = Source(None, None, self.when)
        self.render(source)
        self.assertFalse(source.calls)

    def test_reference_minutes_and_immutable_asset_revision_follow_actual_evidence(self):
        self.inc["reference_minutes"] = [60]
        (video1, _), source = self.render()
        self.assertEqual({c[4] for c in source.calls}, {self.when, self.when - dt.timedelta(minutes=60)})
        (video2, _), _ = self.render()
        self.assertEqual(video1, video2)
        self.inc["where"] = "موقع آخر"
        (video3, _), _ = self.render()
        self.assertNotEqual(video1, video3)


if __name__ == "__main__":
    unittest.main()
