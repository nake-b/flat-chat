"""Integration tests for `TransitOverlayService.route_geometry` (the transit-line
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

from flat_chat.search.transit_overlays import TransitOverlayService

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


async def _seed_stop(
    session: AsyncSession, *, stop_id: str, name: str, lon: float, lat: float, lines
):
    """Insert a transit stop served by `lines` (the line→stop link is the array)."""
    await session.execute(
        sa.text(
            """
            INSERT INTO world.transit_stops
                (stop_id, name, geom, modes_served, lines_served)
            VALUES (
                :sid, :name, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                '{1}', :lines
            )
            """
        ),
        {"sid": stop_id, "name": name, "lon": lon, "lat": lat, "lines": lines},
    )


def test_route_geometry_attaches_served_stations(async_db_url):
    """Stations served by the line ride along as `points`; stops on OTHER lines
    are excluded, and coordinates are rounded to the overlay precision."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-u7",
            short_name="U7",
            wkts=["LINESTRING(13.30 52.50, 13.40 52.48)"],
        )
        await _seed_stop(
            session,
            stop_id="s1",
            name="Rathaus Steglitz",
            lon=13.321234,
            lat=52.456789,
            lines=["U7", "S1"],
        )
        await _seed_stop(
            session,
            stop_id="s2",
            name="Mehringdamm",
            lon=13.387,
            lat=52.493,
            lines=["U7"],
        )
        await _seed_stop(
            session, stop_id="s3", name="Elsewhere", lon=13.50, lat=52.40, lines=["U2"]
        )
        return await TransitOverlayService(session).route_geometry("U7")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    names = {p.label for p in overlay.points}
    assert names == {"Rathaus Steglitz", "Mehringdamm"}  # the U2-only stop excluded
    steglitz = next(p for p in overlay.points if p.label == "Rathaus Steglitz")
    # Coordinates rounded to OVERLAY_COORD_DIGITS (5).
    assert steglitz.lon == 13.32123
    assert steglitz.lat == 52.45679


def test_route_geometry_no_stations_is_empty_points(async_db_url):
    """A line with no mapped stops still draws — `points` is just empty."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-bus",
            short_name="M41",
            wkts=["LINESTRING(13.30 52.50, 13.35 52.49)"],
        )
        return await TransitOverlayService(session).route_geometry("M41")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.points == []


def test_route_geometry_returns_line_geojson(async_db_url):
    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-u7",
            short_name="U7",
            wkts=["LINESTRING(13.30 52.50, 13.35 52.49, 13.40 52.48)"],
        )
        return await TransitOverlayService(session).route_geometry("U7")

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
        return await TransitOverlayService(session).route_geometry("u8")

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
        return await TransitOverlayService(session).route_geometry(
            "U9", origin="pinned"
        )

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.origin == "pinned"
    # ST_Collect of two LineStrings → a MultiLineString.
    assert overlay.geojson["type"] == "MultiLineString"
    assert len(overlay.geojson["coordinates"]) == 2


def test_route_geometry_unknown_line_returns_none(async_db_url):
    async def body(session: AsyncSession):
        svc = TransitOverlayService(session)
        return await svc.route_geometry("X99"), await svc.route_geometry("  ")

    missing, blank = _run(async_db_url, body)
    assert missing is None
    assert blank is None
