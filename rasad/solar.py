"""Vectorized solar illumination and flat-sea sun-glint geometry.

Angles are in degrees. Coordinates refer to a spherical Earth, matching the
geometry used by the monitor. A zero glint angle means the satellite looks in
the direction of specular sunlight reflected by horizontal water; it is not a
prediction that the sea is actually reflective or cloud-free.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

EARTH_RADIUS_KM = 6371.0
GEO_ORBIT_RADIUS_KM = 42164.0


def _utc(when):
    if isinstance(when, str):
        when = dt.datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc)


def _sun_vector(when):
    """Unit vector toward the Sun in Earth-centered, Earth-fixed coordinates."""
    when = _utc(when)
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    gamma = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    equation_min = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
                           - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2 * gamma)
                           - 0.040849 * math.sin(2 * gamma))
    subsolar_lon = math.radians(180 - 15 * hour - equation_min / 4)
    return np.array([math.cos(decl) * math.cos(subsolar_lon),
                     math.cos(decl) * math.sin(subsolar_lon), math.sin(decl)])


def _surface_normals(lat, lon):
    lat, lon = np.broadcast_arrays(np.asarray(lat, dtype=np.float64),
                                  np.asarray(lon, dtype=np.float64))
    if not np.isfinite(lat).all() or not np.isfinite(lon).all() or (np.abs(lat) > 90).any():
        raise ValueError("latitude/longitude must be finite and latitude must lie within [-90, 90]")
    latitude, longitude = np.radians(lat), np.radians(lon)
    cos_lat = np.cos(latitude)
    return np.stack((cos_lat * np.cos(longitude), cos_lat * np.sin(longitude),
                     np.sin(latitude)), axis=-1)


def _result(value):
    return float(value) if np.ndim(value) == 0 else value


def sun_elevation_grid(lat, lon, t):
    """Solar elevation for broadcastable scalar/array coordinates and a UTC time.

    A timezone-aware time is converted to UTC; a naive time is treated as UTC.
    Uses the same NOAA approximation as :func:`util.sun_elevation`.
    """
    normals = _surface_normals(lat, lon)
    cos_zenith = np.einsum("...i,i->...", normals, _sun_vector(t))
    return _result(np.degrees(np.arcsin(np.clip(cos_zenith, -1.0, 1.0))))


def solar_geometry(lat, lon, t, satellite_lon=0.0, satellite_lat=0.0,
                   orbit_radius_km=GEO_ORBIT_RADIUS_KM):
    """Return ``(sun elevation, satellite elevation, glint angle)`` in degrees.

    The orbit radius is measured from Earth's center, not from its surface.
    Glint is NaN unless both the Sun and satellite are above the local horizon.
    Outputs are Python floats for scalar inputs, otherwise broadcast NumPy arrays.

    For surface normal N, sun direction S and view direction V, reflection is
    ``2 (S·N) N - S``. The implementation evaluates its dot product with V
    directly, avoiding several extra three-component arrays for large images.
    """
    orbit_radius_km = float(orbit_radius_km)
    satellite_lon, satellite_lat = float(satellite_lon), float(satellite_lat)
    if (not math.isfinite(orbit_radius_km) or orbit_radius_km <= EARTH_RADIUS_KM
            or not math.isfinite(satellite_lon) or not math.isfinite(satellite_lat)
            or abs(satellite_lat) > 90):
        raise ValueError("satellite position must be finite and outside Earth")
    normals = _surface_normals(lat, lon)
    sun = _sun_vector(t)
    slat, slon = math.radians(satellite_lat), math.radians(satellite_lon)
    satellite = orbit_radius_km * np.array([math.cos(slat) * math.cos(slon),
                                          math.cos(slat) * math.sin(slon), math.sin(slat)])
    sun_dot_normal = np.einsum("...i,i->...", normals, sun)
    satellite_dot_normal = np.einsum("...i,i->...", normals, satellite)
    distance = np.sqrt(orbit_radius_km ** 2 + EARTH_RADIUS_KM ** 2
                       - 2 * EARTH_RADIUS_KM * satellite_dot_normal)
    view_dot_normal = (satellite_dot_normal - EARTH_RADIUS_KM) / distance
    sun_dot_view = (float(np.dot(satellite, sun)) - EARTH_RADIUS_KM * sun_dot_normal) / distance
    reflection_dot_view = 2 * sun_dot_normal * view_dot_normal - sun_dot_view
    elevation = np.degrees(np.arcsin(np.clip(sun_dot_normal, -1.0, 1.0)))
    view_elevation = np.degrees(np.arcsin(np.clip(view_dot_normal, -1.0, 1.0)))
    glint = np.degrees(np.arccos(np.clip(reflection_dot_view, -1.0, 1.0)))
    glint = np.where((elevation > 0) & (view_elevation > 0), glint, np.nan)
    return tuple(_result(value) for value in (elevation, view_elevation, glint))


def glint_angle(lat, lon, t, satellite_lon=0.0, satellite_lat=0.0,
                orbit_radius_km=GEO_ORBIT_RADIUS_KM):
    """Angle from flat-water specular reflection; see :func:`solar_geometry`."""
    return solar_geometry(lat, lon, t, satellite_lon, satellite_lat, orbit_radius_km)[2]
