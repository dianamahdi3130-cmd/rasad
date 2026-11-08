"""Illumination and reflection checks for the daytime sea mask."""
import datetime as dt
import math
import unittest
from unittest.mock import patch

import numpy as np

from rasad import solar
from rasad.util import UTC, sun_elevation


class SolarGeometryTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 11, 8, 5, 50, tzinfo=UTC)

    def test_solar_elevation_matches_existing_noaa_calculation(self):
        for when in (self.WHEN, dt.datetime(2026, 6, 21, 12, tzinfo=UTC),
                     dt.datetime(2026, 12, 21, 0, tzinfo=UTC)):
            for lat, lon in ((26.04, 55.15), (24.35, 59.84), (0, 0), (-45, -120), (90, 0)):
                with self.subTest(when=when, lat=lat, lon=lon):
                    self.assertAlmostEqual(solar.sun_elevation_grid(lat, lon, when),
                                           sun_elevation(lat, lon, when), places=9)

    def test_scalar_array_and_broadcast_results_agree(self):
        lat = np.array([[24.0], [26.0]])
        lon = np.array([[51.0, 55.0, 60.0]])
        arrays = solar.solar_geometry(lat, lon, self.WHEN)
        self.assertTrue(all(a.shape == (2, 3) for a in arrays))
        for row in range(2):
            for col in range(3):
                scalar = solar.solar_geometry(lat[row, 0], lon[0, col], self.WHEN)
                np.testing.assert_allclose([a[row, col] for a in arrays], scalar, atol=1e-10)
        self.assertIsInstance(solar.sun_elevation_grid(26, 55, self.WHEN), float)
        self.assertIsInstance(solar.glint_angle(26, 55, self.WHEN), float)

    def test_utc_offset_is_normalized(self):
        local = self.WHEN.astimezone(dt.timezone(dt.timedelta(hours=3)))
        np.testing.assert_allclose(solar.solar_geometry(24.35, 59.84, local),
                                   solar.solar_geometry(24.35, 59.84, self.WHEN))
        self.assertAlmostEqual(solar.sun_elevation_grid(26, 55, local.isoformat()),
                               solar.sun_elevation_grid(26, 55, self.WHEN))

    def test_overhead_sun_and_nadir_view_have_zero_glint_angle(self):
        with patch.object(solar, "_sun_vector", return_value=np.array([1.0, 0.0, 0.0])):
            elevation, view, angle = solar.solar_geometry(0, 0, self.WHEN)
        self.assertEqual(elevation, 90.0)
        self.assertEqual(view, 90.0)
        self.assertAlmostEqual(angle, 0.0, places=5)

    def test_oblique_specular_geometry_and_wrong_sun_azimuth(self):
        # At the equator, an eastward satellite and equally inclined westward Sun
        # share the specular ray. Reversing the Sun's azimuth must remove that match.
        radius = solar.GEO_ORBIT_RADIUS_KM
        beta = math.atan2(radius * math.sin(math.radians(30)),
                          radius * math.cos(math.radians(30)) - solar.EARTH_RADIUS_KM)
        correct_sun = np.array([math.cos(beta), -math.sin(beta), 0.0])
        wrong_sun = np.array([math.cos(beta), math.sin(beta), 0.0])
        with patch.object(solar, "_sun_vector", return_value=correct_sun):
            elevation, view, angle = solar.solar_geometry(0, 0, self.WHEN, satellite_lon=30)
            self.assertAlmostEqual(elevation, view, places=9)
            self.assertAlmostEqual(angle, 0, places=5)
        with patch.object(solar, "_sun_vector", return_value=wrong_sun):
            angle = solar.glint_angle(0, 0, self.WHEN, satellite_lon=30)
        self.assertAlmostEqual(angle, 2 * math.degrees(beta), places=8)

    def test_night_and_unseen_satellite_do_not_produce_a_valid_glint_score(self):
        with patch.object(solar, "_sun_vector", return_value=np.array([-1.0, 0.0, 0.0])):
            elevation, view, angle = solar.solar_geometry([0, 0], [0, 180], self.WHEN)
        self.assertLess(elevation[0], 0)
        self.assertGreater(view[0], 0)
        self.assertGreater(elevation[1], 0)
        self.assertLess(view[1], 0)
        self.assertTrue(np.isnan(angle).all())

    def test_points_beyond_geostationary_limb_are_masked(self):
        with patch.object(solar, "_sun_vector", return_value=np.array([0.0, 1.0, 0.0])):
            elevation, view, angle = solar.solar_geometry(0, 90, self.WHEN)
        self.assertAlmostEqual(elevation, 90, places=9)
        self.assertLess(view, 0)
        self.assertTrue(math.isnan(angle))

    def test_gulf_locations_are_daylit_and_visible_at_morning_publication_times(self):
        cases = [(24.35, 59.84, "2026-11-08T03:40:00Z", 17.87),
                 (26.04, 55.15, "2026-11-08T05:50:00Z", 36.65)]
        for lat, lon, when, expected_elevation in cases:
            with self.subTest(lat=lat, lon=lon):
                elevation, view, angle = solar.solar_geometry(lat, lon, when)
                self.assertAlmostEqual(elevation, expected_elevation, delta=0.15)
                self.assertGreater(view, 10)
                self.assertTrue(0 <= angle <= 180)

    def test_mirrored_equatorial_reflection_has_same_glint_angle(self):
        # Reflect the Sun, ground point and satellite around the Greenwich plane.
        sun = np.array([0.8, 0.6, 0.0])
        with patch.object(solar, "_sun_vector", return_value=sun):
            first = solar.solar_geometry(0, 15, self.WHEN, satellite_lon=35)
        with patch.object(solar, "_sun_vector", return_value=sun * [1, -1, 1]):
            second = solar.solar_geometry(0, -15, self.WHEN, satellite_lon=-35)
        np.testing.assert_allclose(first, second, atol=1e-10)

    def test_invalid_coordinates_and_satellite_positions_are_rejected(self):
        for lat, lon in ((91, 0), (float("nan"), 0), (0, float("inf"))):
            with self.subTest(lat=lat, lon=lon), self.assertRaises(ValueError):
                solar.sun_elevation_grid(lat, lon, self.WHEN)
        for kwargs in ({"orbit_radius_km": 6000}, {"satellite_lat": 91},
                       {"satellite_lon": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                solar.solar_geometry(0, 0, self.WHEN, **kwargs)


if __name__ == "__main__":
    unittest.main()
