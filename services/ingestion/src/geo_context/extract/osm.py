"""OpenStreetMap landmark extractor (Overpass API).

A second extraction modality alongside the Berlin GDI WFS (`wfs.py`) and the
VBB GTFS feed (`gtfs.py`). ALKIS building footprints (loaded via WFS into the
`landmarks` table) are rich — they name the Fernsehturm, Siegessäule, TU
Berlin — but miss free-standing *Bauwerke* like the Brandenburger Tor, the
Olympiastadion bowl and named bridges. OSM fills that gap.

This module queries Overpass for a fixed set of landmark tags scoped to the
Berlin admin area, preserves each feature's NATIVE geometry (a node stays a
point, a bridge way stays a line, an area stays a polygon — no centroid
flattening), and returns a GeoDataFrame ready to APPEND into the same
`landmarks` table the WFS loader seeds, tagged `source='osm'` with a
`category` derived from the matched tag.

Attribution: OSM data is ODbL — the frontend surfaces
"© OpenStreetMap contributors".

Overpass is a free, shared, frequently-overloaded service, so the query is
wrapped in retry/backoff. If it stays flaky in practice, the durable fix is a
local Geofabrik Berlin extract (osm.pbf) filtered offline — see TODO below.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import requests
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

SILVER_SRID = 4326

# Public Overpass instance. A heavily-shared free service; flaky under load.
# TODO: fall back to a local Geofabrik Berlin extract (osm.pbf) filtered
# offline with osmium/pyrosm when Overpass throttling becomes a CI/ETL
# reliability problem — same output shape, no network dependency.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Berlin's OSM administrative relation id (admin_level=4). The Overpass
# `area(<3600000000 + relation_id>)` convention scopes every query clause to
# the Berlin boundary so we never pull Brandenburg features.
_BERLIN_RELATION_ID = 62422
_BERLIN_AREA_ID = 3_600_000_000 + _BERLIN_RELATION_ID

# Landmark tag selectors → the `category` stored on each row. Each maps one
# OSM key=value to a canonical category; the Overpass query unions a clause
# per entry across nodes / ways / relations.
_TAG_CATEGORIES: dict[tuple[str, str], str] = {
    ("historic", "monument"): "monument",
    ("historic", "memorial"): "memorial",
    ("man_made", "tower"): "tower",
    ("man_made", "bridge"): "bridge",
    ("tourism", "attraction"): "attraction",
    ("leisure", "stadium"): "stadium",
}

# Overpass HTTP statuses worth retrying: 429 (too many requests — the gateway
# rate limit) and the 5xx family it emits when a slot queue is saturated or a
# query times out server-side.
_RETRYABLE_STATUS = {429, 502, 503, 504}
_MAX_ATTEMPTS = 5


class _RetryableOverpassError(Exception):
    """Transient Overpass failure — safe to retry with backoff."""


def _build_query() -> str:
    """Compose the Overpass QL query scoped to the Berlin admin area.

    Emits a union of `nodes` / `ways` / `relations` for every tag selector,
    then `out tags geom` so each element carries its tag set + native
    geometry (lat/lon for nodes, a point list for ways, members for
    relations).
    """
    clauses = "".join(
        f'  node["{key}"="{value}"](area.berlin);\n'
        f'  way["{key}"="{value}"](area.berlin);\n'
        f'  relation["{key}"="{value}"](area.berlin);\n'
        for (key, value) in _TAG_CATEGORIES
    )
    return (
        "[out:json][timeout:120];\n"
        f"area({_BERLIN_AREA_ID})->.berlin;\n"
        "(\n"
        f"{clauses}"
        ");\n"
        "out tags geom;\n"
    )


def _category_for(tags: dict[str, str]) -> str | None:
    """Resolve the canonical category from an element's tag set.

    First matching selector wins (dict insertion order). Returns None if the
    element matched the query but carries none of our key=value pairs (e.g. a
    relation pulled in as a member) — the caller drops it.
    """
    for (key, value), category in _TAG_CATEGORIES.items():
        if tags.get(key) == value:
            return category
    return None


def _geometry_for(element: dict) -> Point | LineString | Polygon | MultiPolygon | None:
    """Build native shapely geometry from an Overpass element.

    - node → Point(lon, lat)
    - way  → LineString of its `geometry` point list; closed ways (first ==
             last point) become a Polygon (areas like a stadium bowl).
    - relation → MultiPolygon of its outer member ways (best-effort; skipped
             if it has no usable closed members).

    Returns None when the element has no geometry to build (e.g. an empty
    way) so the caller can skip it.
    """
    el_type = element.get("type")
    if el_type == "node":
        lon, lat = element.get("lon"), element.get("lat")
        if lon is None or lat is None:
            return None
        return Point(lon, lat)

    if el_type == "way":
        coords = [(p["lon"], p["lat"]) for p in element.get("geometry") or []]
        if len(coords) < 2:
            return None
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return Polygon(coords)
        return LineString(coords)

    if el_type == "relation":
        rings: list[Polygon] = []
        for member in element.get("members") or []:
            if member.get("type") != "way" or member.get("role") != "outer":
                continue
            coords = [(p["lon"], p["lat"]) for p in member.get("geometry") or []]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                rings.append(Polygon(coords))
        if not rings:
            return None
        return MultiPolygon(rings)

    return None


class OverpassClient:
    """Thin Overpass API client returning a landmarks-shaped GeoDataFrame."""

    def __init__(
        self,
        url: str = OVERPASS_URL,
        http_timeout_s: float = 180.0,
    ) -> None:
        self.url = url
        self.http_timeout_s = http_timeout_s

    @retry(
        retry=retry_if_exception_type(
            (_RetryableOverpassError, requests.ConnectionError, requests.Timeout)
        ),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        reraise=True,
    )
    def _post(self, query: str) -> dict:
        """POST the Overpass query, retrying transient 429/5xx + transport errors.

        A non-retryable 4xx (e.g. 400 malformed query) raises on the first
        attempt so a real bug surfaces immediately instead of being masked by
        backoff.
        """
        # Overpass rejects the default python-requests User-Agent with HTTP 406
        # Not Acceptable. A descriptive UA (also Overpass etiquette) + explicit
        # JSON Accept are required.
        resp = requests.post(
            self.url,
            data={"data": query},
            headers={
                "User-Agent": "flat-chat-geo-context/1.0 (Berlin apartment search ETL)",
                "Accept": "application/json",
            },
            timeout=self.http_timeout_s,
        )
        if resp.status_code in _RETRYABLE_STATUS:
            logger.warning(
                "overpass: transient %d — retrying with backoff", resp.status_code
            )
            raise _RetryableOverpassError(f"status {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    def fetch_landmarks(self) -> gpd.GeoDataFrame:
        """Fetch OSM landmarks for Berlin as a `landmarks`-shaped GeoDataFrame.

        Columns: `name`, `description` (None — OSM has no ALKIS Bezeichnung
        equivalent), `category`, `geom`, in EPSG:4326. `source='osm'` is
        injected by the loader, not here, mirroring the WFS `extra` convention.
        Rows without a `name` or without buildable geometry are dropped.
        """
        query = _build_query()
        logger.info(
            "overpass: fetching Berlin landmarks (%d tag selectors)",
            len(_TAG_CATEGORIES),
        )
        payload = self._post(query)
        elements = payload.get("elements") or []
        logger.info("overpass: received %d raw elements", len(elements))

        names: list[str] = []
        categories: list[str] = []
        geometries: list[Point | LineString | Polygon | MultiPolygon] = []
        for element in elements:
            tags = element.get("tags") or {}
            name = (tags.get("name") or "").strip()
            if not name:
                continue
            category = _category_for(tags)
            if category is None:
                continue
            geometry = _geometry_for(element)
            if geometry is None:
                continue
            names.append(name)
            categories.append(category)
            geometries.append(geometry)

        gdf = gpd.GeoDataFrame(
            {"name": names, "category": categories, "geometry": geometries},
            geometry="geometry",
            crs=f"EPSG:{SILVER_SRID}",
        )
        # `description` has no OSM source; keep the column for `landmarks`
        # parity (ALKIS supplies it via the `bezeich` alias).
        gdf["description"] = None
        gdf = gdf.rename_geometry("geom")
        logger.info("overpass: %d named landmarks with geometry", len(gdf))
        return gdf
