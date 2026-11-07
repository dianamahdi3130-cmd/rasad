import datetime as dt
import unittest
from unittest.mock import Mock

from PIL import Image

from rasad.sources import detect_geo_layer
from rasad.util import UTC, iso


class GeoProductTimeTests(unittest.TestCase):
    def setUp(self):
        self.when = dt.datetime(2026, 11, 7, 21, 30, tzinfo=UTC)
        self.layer = {"name": "test:frp", "id": "test_frp", "family": "mtg", "tile_px": 16}
        self.tile = [50, 24, 51, 25]
        self.image = Image.new("RGBA", (16, 16))
        self.image.putpixel((8, 8), (255, 0, 0, 255))
        self.wms = Mock()

    def test_exact_product_time_is_read(self):
        self.wms.getmap.return_value = (self.image, self.when)

        detections = detect_geo_layer(self.wms, self.layer, self.tile, self.when)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["t"], iso(self.when))
        self.assertEqual(detections[0]["_fi"][-1], iso(self.when))

    def test_substituted_older_product_is_left_for_retry(self):
        older = self.when - dt.timedelta(minutes=10)
        self.wms.getmap.return_value = (self.image, older)

        self.assertIsNone(detect_geo_layer(self.wms, self.layer, self.tile, self.when))

        # Once the requested product arrives, the same slot can yield its own detection.
        self.wms.getmap.return_value = (self.image, self.when)
        self.assertEqual(len(detect_geo_layer(self.wms, self.layer, self.tile, self.when)), 1)

    def test_small_timestamp_offset_is_used_for_strength_query(self):
        actual = self.when + dt.timedelta(seconds=120)
        self.wms.getmap.return_value = (self.image, actual)

        detections = detect_geo_layer(self.wms, self.layer, self.tile, self.when)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["t"], iso(actual))
        self.assertEqual(detections[0]["_fi"][-1], iso(actual))

    def test_product_without_substitution_header_uses_requested_time(self):
        self.wms.getmap.return_value = (self.image, None)

        detections = detect_geo_layer(self.wms, self.layer, self.tile, self.when)

        self.assertEqual(detections[0]["t"], iso(self.when))
        self.assertEqual(detections[0]["_fi"][-1], iso(self.when))


if __name__ == "__main__":
    unittest.main()
