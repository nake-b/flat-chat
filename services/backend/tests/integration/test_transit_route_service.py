"""Integration tests for `TransitRouteService.route_geometry` (the transit-line
map-overlay read path).

Resolves a human line name ("U7") → route_id(s) → the canonical per-direction
LineStrings, collected + simplified into one GeoJSON geometry. EXECUTES against
PostGIS — ST_Collect / ST_AsGeoJSON / ST_SimplifyPreserveTopology and the
case-insensitive `upper(short_name)` match only exist there. Each test seeds
`world.transit_routes` + `world.transit_route_shapes` and rolls back.

This path is display-only and DELIBERATELY separate from search's transit
filter (which matches stops via listings_nearby_transit, not the centerline).
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.search.transit_routes import TransitRouteService

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED


def _run(async_url: str, body):
    async def _wrapped():
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                trans = await conn.begin()
                try:
                    session = AsyncSession(bind=conn, expire_on_commit=False)
                    try:
                        return await body(session)
                    finally:
                        await session.close()
                finally:
                    await trans.rollback()
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


async def _seed_line(session: AsyncSession, *, route_id: str, short_name: str, wkts):
    """Insert a route + one shape per direction (WKT LineStrings)."""
    await session.execute(
        sa.text(
            "INSERT INTO world.transit_routes (route_id, short_name, route_type) "
            "VALUES (:rid, :sn, 1)"
        ),
        {"rid": route_id, "sn": short_name},
    )
    for direction_id, wkt in enumerate(wkts):
        await session.execute(
            sa.text(
                """
                INSERT INTO world.transit_route_shapes (route_id, direction_id, geom)
                VALUES (:rid, :dir, ST_SetSRID(ST_GeomFromText(:wkt), 4326))
                """
            ),
            {"rid": route_id, "dir": direction_id, "wkt": wkt},
        )


def test_route_geometry_returns_line_geojson(async_db_url):
    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-u7",
            short_name="U7",
            wkts=["LINESTRING(13.30 52.50, 13.35 52.49, 13.40 52.48)"],
        )
        return await TransitRouteService(session).route_geometry("U7")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.kind == "transit_line"
    assert overlay.label == "U7"
    assert overlay.id == "transit_line:U7"
    assert overlay.geojson["type"] in ("LineString", "MultiLineString")


def test_route_geometry_is_case_insensitive(async_db_url):
    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-u8",
            short_name="U8",
            wkts=["LINESTRING(13.38 52.55, 13.40 52.52)"],
        )
        return await TransitRouteService(session).route_geometry("u8")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.label == "U8"


def test_route_geometry_collects_both_directions(async_db_url):
    """Two directions for one line collect into a single MultiLineString."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-u9",
            short_name="U9",
            wkts=[
                "LINESTRING(13.30 52.50, 13.35 52.50)",
                "LINESTRING(13.35 52.50, 13.30 52.50)",
            ],
        )
        return await TransitRouteService(session).route_geometry("U9", origin="pinned")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.origin == "pinned"
    # ST_Collect of two LineStrings → a MultiLineString.
    assert overlay.geojson["type"] == "MultiLineString"
    assert len(overlay.geojson["coordinates"]) == 2


def test_route_geometry_unknown_line_returns_none(async_db_url):
    async def body(session: AsyncSession):
        svc = TransitRouteService(session)
        return await svc.route_geometry("X99"), await svc.route_geometry("  ")

    missing, blank = _run(async_db_url, body)
    assert missing is None
    assert blank is None
