"""Small, dependency-free geometry helpers for in-memory point math.

Leaf layer — imported by `routing/` for the transit last-mile loop (each
listing's time = min over nearby stops of anchor→stop + walk(stop→listing)),
which runs over thousands of stop×listing pairs and must stay cheap in Python.

Use `equirect_distance_m` for that hot loop, NOT a great-circle formula: at
Berlin's scale (~40 km span, and last-mile hops under ~2 km) the equirectangular
approximation is within a fraction of a percent of the geodesic while being a
handful of float ops. Distance that must be exact against a real shape (the
distance *lens*) uses PostGIS `ST_Distance` in `DistanceService` instead — this
helper is only for the in-memory ranking loop.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def equirect_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular ("flat-earth") distance in metres between two lat/lon
    points. Negligible error vs. the geodesic at city scale; no dependency."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    x = math.radians(lon2 - lon1) * math.cos(mean_lat)
    y = math.radians(lat2 - lat1)
    return EARTH_RADIUS_M * math.hypot(x, y)
