"""Channel extraction must preserve night values and share time-validated WMS requests."""
import datetime as dt
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from rasad.seawatch import Pictures
from rasad.util import iso


class DayPicturesTests(unittest.TestCase):
    def setUp(self):
        self.when = dt.datetime(2026, 11, 8, 6, tzinfo=dt.timezone.utc)
        pixels = np.full((4, 4, 4), [90, 30, 6, 255], dtype=np.uint8)
        pixels[0, 0, 3] = 0
        self.image = Image.fromarray(pixels)
        self.wms = Mock()
        self.wms.getmap.return_value = self.image, self.when
        self.pics = Pictures(self.wms)
        self.args = ("fire-rgb", [55, 25, 56, 26], 4, 4, self.when)

    def test_red_blue_and_night_mean_share_one_download(self):
        self.pics.prefetch([self.args + (0,), self.args, self.args + (2,)])
        red = self.pics.get(*self.args, 0)
        blue = self.pics.get(*self.args, 2)
        night = self.pics.get(*self.args)
        self.wms.getmap.assert_called_once()
        self.assertEqual((red[1, 1], blue[1, 1], night[1, 1]), (90, 6, 42))
        self.assertTrue(all(np.isnan(a[0, 0]) for a in (red, blue, night)))

    def test_wrong_time_is_missing_for_every_channel(self):
        self.wms.getmap.return_value = self.image, self.when - dt.timedelta(minutes=10)
        for channel in (0, 2, None):
            self.assertIsNone(self.pics.get(*self.args, channel))
        self.wms.getmap.assert_called_once()

    def test_offline_probe_can_prime_the_original_grayscale_cache(self):
        gray = np.ones((4, 4), dtype=np.float32)
        key = (self.args[0], tuple(self.args[1]), 4, 4, iso(self.when))
        self.pics.cache[key] = gray
        self.assertIs(self.pics.get(*self.args), gray)
        self.wms.getmap.assert_not_called()

    def test_download_retry_is_shared_and_slot_reset_retries_missing_data(self):
        self.wms.getmap.side_effect = [(None, None), (None, None), (self.image, self.when)]
        self.assertIsNone(self.pics.get(*self.args, 0))
        self.assertIsNone(self.pics.get(*self.args, 2))
        self.assertEqual(self.wms.getmap.call_count, 2)
        self.pics.cache = {}
        self.assertEqual(self.pics.get(*self.args, 0)[1, 1], 90)
        self.assertEqual(self.wms.getmap.call_count, 3)


if __name__ == "__main__":
    unittest.main()
