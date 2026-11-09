"""Short scan cycles can reuse complete, matching pictures without freezing missing data."""
import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image, PngImagePlugin

from rasad import seawatch
from rasad.engine import DEFAULTS
from rasad.util import UTC, iso


class PictureCacheTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 11, 9, 7, tzinfo=UTC)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "pictures"
        self.image = Image.fromarray(np.full((4, 4, 4), [90, 30, 6, 255], dtype=np.uint8))
        self.wms = Mock()
        self.wms.getmap.side_effect = lambda *args, **kwargs: (self.image, kwargs["t"])
        self.args = ("fire-rgb", [55, 25, 56, 26], 4, 4, self.WHEN)

    def pictures(self):
        return seawatch.Pictures(self.wms, cache_dir=self.folder)

    def cached_file(self):
        files = list(self.folder.glob("*.png"))
        self.assertEqual(len(files), 1)
        return files[0]

    def corrupt_image(self, image=None, **metadata_changes):
        path = self.cached_file()
        with Image.open(path) as original:
            metadata = dict(original.info)
            raw = original.copy()
        metadata.update(metadata_changes)
        info = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            info.add_text(key, value)
        (image or raw).save(path, pnginfo=info)

    def test_new_instance_reuses_original_channels_without_another_http_request(self):
        first = self.pictures()
        self.assertEqual(first.get(*self.args, 0)[1, 1], 90)
        self.assertEqual(len(list(self.folder.iterdir())), 1, "atomic temporary file is removed")
        second = self.pictures()
        second.prefetch([self.args + (0,), self.args + (2,), self.args])
        self.assertEqual(second.get(*self.args, 0)[1, 1], 90)
        self.assertEqual(second.get(*self.args, 2)[1, 1], 6)
        self.assertEqual(second.get(*self.args)[1, 1], 42)
        self.wms.getmap.assert_called_once()

    def test_no_substitute_report_is_preserved_without_inventing_an_actual_time(self):
        self.wms.getmap.side_effect = None
        self.wms.getmap.return_value = self.image, None
        self.pictures().get(*self.args)
        with Image.open(self.cached_file()) as image:
            self.assertEqual(image.info["rasad_actual"], "")
            self.assertEqual(image.info["rasad_requested"], iso(self.WHEN))
        self.pictures().get(*self.args, 2)
        self.wms.getmap.assert_called_once()

    def test_changing_the_wms_service_cannot_reuse_another_services_pixels(self):
        self.wms.url = "https://first.example/wms"
        self.pictures().get(*self.args, 0)
        self.wms.url = "https://second.example/wms"
        self.pictures().get(*self.args, 0)
        self.assertEqual(self.wms.getmap.call_count, 2)
        self.assertEqual(len(list(self.folder.glob("*.png"))), 2)

    def test_partial_alpha_is_available_in_memory_but_never_persisted(self):
        pixels = np.asarray(self.image).copy()
        pixels[0, 0, 3] = 0
        self.image = Image.fromarray(pixels)
        first = self.pictures().get(*self.args, 0)
        self.assertTrue(np.isnan(first[0, 0]))
        self.assertEqual(first[1, 1], 90)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.pictures().get(*self.args, 0)
        self.assertEqual(self.wms.getmap.call_count, 2)

    def test_missing_wrong_grid_and_wrong_time_are_never_persisted(self):
        for image, actual in ((None, None),
                              (Image.new("RGBA", (3, 4), (1, 2, 3, 255)), self.WHEN),
                              (self.image, self.WHEN - dt.timedelta(minutes=10))):
            with self.subTest(actual=actual, image_size=image.size if image else None):
                self.wms.getmap.side_effect = None
                self.wms.getmap.return_value = image, actual
                self.assertIsNone(self.pictures().get(*self.args))
                self.assertEqual(list(self.folder.iterdir()), [])

    def test_corrupt_png_is_refetched_instead_of_reused(self):
        self.pictures().get(*self.args)
        self.cached_file().write_bytes(b"not a complete PNG")
        self.assertEqual(self.pictures().get(*self.args, 0)[1, 1], 90)
        self.assertEqual(self.wms.getmap.call_count, 2)
        with Image.open(self.cached_file()) as image:
            self.assertEqual(image.size, (4, 4))

    def test_cache_request_and_actual_time_must_match(self):
        for metadata in ({"rasad_request": "another layer or area"},
                         {"rasad_actual": iso(self.WHEN - dt.timedelta(minutes=10))},
                         {"rasad_requested": iso(self.WHEN + dt.timedelta(minutes=10))}):
            with self.subTest(metadata=metadata):
                self.pictures().get(*self.args)
                self.corrupt_image(**metadata)
                before = self.wms.getmap.call_count
                self.pictures().get(*self.args, 0)
                self.assertEqual(self.wms.getmap.call_count, before + 1)

    def test_wrong_grid_and_partial_alpha_on_disk_are_rejected(self):
        partial = np.asarray(self.image).copy()
        partial[0, 0, 3] = 0
        for image in (Image.new("RGBA", (3, 4), (1, 2, 3, 255)), Image.fromarray(partial)):
            with self.subTest(image_size=image.size):
                self.pictures().get(*self.args)
                self.corrupt_image(image=image)
                before = self.wms.getmap.call_count
                result = self.pictures().get(*self.args, 0)
                self.assertTrue(np.isfinite(result).all())
                self.assertEqual(self.wms.getmap.call_count, before + 1)

    def test_old_cache_and_abandoned_temporary_files_are_pruned(self):
        self.pictures().get(*self.args)
        old = self.cached_file()
        abandoned = self.folder / ".rasad-picture-interrupted.tmp"
        abandoned.write_bytes(b"partial")
        foreign = self.folder / "leave-this.txt"
        foreign.write_text("other files are not cache entries")
        stamp = time.time() - seawatch.Pictures.CACHE_TTL_S - 1
        for path in (old, abandoned):
            os.utime(path, (stamp, stamp))
        current = self.pictures()
        self.assertFalse(old.exists())
        self.assertFalse(abandoned.exists())
        self.assertTrue(foreign.exists())
        current.get(*self.args)
        self.assertEqual(self.wms.getmap.call_count, 2)

    def test_capacity_evicts_oldest_entries_without_exceeding_limit(self):
        pictures = self.pictures()
        pictures.get(*self.args)
        first = self.cached_file()
        maximum = first.stat().st_size + 1
        stamp = time.time() - 60
        os.utime(first, (stamp, stamp))
        newer = (*self.args[:4], self.WHEN + dt.timedelta(minutes=10))
        with patch.object(seawatch.Pictures, "CACHE_MAX_BYTES", maximum):
            pictures.get(*newer)
        self.assertFalse(first.exists())
        files = list(self.folder.glob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertLessEqual(sum(path.stat().st_size for path in files), maximum)

    def test_failed_atomic_write_does_not_leave_a_partial_cached_file(self):
        def failed_save(image, filename, *args, **kwargs):
            Path(filename).write_bytes(b"interrupted PNG")
            raise OSError("disk temporarily unavailable")

        with patch.object(Image.Image, "save", failed_save):
            result = self.pictures().get(*self.args, 0)
        self.assertEqual(result[1, 1], 90)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.pictures().get(*self.args)
        self.assertEqual(self.wms.getmap.call_count, 2)

    def test_background_watch_cache_is_outside_state_and_site_folders(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(_background_media=True, _state_dir=self.root / "state")
        cfg["seawatch"]["boxes"] = []
        with patch.object(seawatch, "Pictures") as pictures:
            seawatch.watch(cfg, Mock(), {}, self.WHEN, avail=Mock())
        pictures.assert_called_once_with(Mock if False else pictures.call_args.args[0],
                                         cache_dir=self.root / ".rasad-pictures")


if __name__ == "__main__":
    unittest.main()
