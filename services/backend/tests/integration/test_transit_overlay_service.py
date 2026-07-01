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


def _coords(geojson: dict) -> list[tuple[float, float]]:
    """Flatten a LineString / MultiLineString geometry to (lon, lat) pairs."""
    if geojson["type"] == "LineString":
        return [(c[0], c[1]) for c in geojson["coordinates"]]
    return [(c[0], c[1]) for line in geojson["coordinates"] for c in line]


async def _seed_line(
    session: AsyncSession,
    *,
    route_id: str,
    short_name: str,
    wkts,
    route_type: int = 1,
):
    """Insert a route + one shape per direction (WKT LineStrings).

    `route_type` lets a test seed several routes under one `short_name` in
    different modes (e.g. an S-Bahn line plus a replacement bus). `wkts` may be
    empty to seed a route with no shape at all.
    """
    await session.execute(
        sa.text(
            "INSERT INTO world.transit_routes (route_id, short_name, route_type) "
            "VALUES (:rid, :sn, :rt)"
        ),
        {"rid": route_id, "sn": short_name, "rt": route_type},
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
            wkts=["LINESTRING(13.30 52.50, 13.40 52.50)"],
        )
        # On the line (~50 m off) and claims U7 → kept.
        await _seed_stop(
            session,
            stop_id="s1",
            name="Rathaus Steglitz",
            lon=13.321234,
            lat=52.500449,
            lines=["U7", "S1"],
        )
        await _seed_stop(
            session,
            stop_id="s2",
            name="Mehringdamm",
            lon=13.387,
            lat=52.5008,
            lines=["U7"],
        )
        # Claims a different line → excluded by membership.
        await _seed_stop(
            session, stop_id="s3", name="Elsewhere", lon=13.35, lat=52.50, lines=["U2"]
        )
        return await TransitOverlayService(session).route_geometry("U7")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    names = {p.label for p in overlay.points}
    assert names == {"Rathaus Steglitz", "Mehringdamm"}  # the U2-only stop excluded
    steglitz = next(p for p in overlay.points if p.label == "Rathaus Steglitz")
    # Coordinates rounded to OVERLAY_COORD_DIGITS (5) AND snapped onto the line
    # (the line sits at lat 52.50, so the dot's lat snaps there from 52.500449).
    assert steglitz.lon == 13.32123
    assert steglitz.lat == 52.5


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


def test_route_geometry_drops_replacement_bus_mode(async_db_url):
    """A line name shared by S-Bahn routes (route_type 109) and a replacement
    bus (route_type 700) draws only the dominant mode — the bus shape, on
    entirely different coordinates, must not appear (issue #29)."""

    async def body(session: AsyncSession):
        for rid, wkt in [
            ("r-s7-a", "LINESTRING(13.10 52.50, 13.25 52.50)"),  # longest 109
            ("r-s7-b", "LINESTRING(13.10 52.50, 13.20 52.50)"),
            ("r-s7-c", "LINESTRING(13.12 52.50, 13.18 52.50)"),
            ("r-s7-d", "LINESTRING(13.13 52.50, 13.17 52.50)"),
        ]:
            await _seed_line(
                session, route_id=rid, short_name="S7", wkts=[wkt], route_type=109
            )
        await _seed_line(
            session,
            route_id="r-s7-bus",
            short_name="S7",
            wkts=["LINESTRING(13.80 52.30, 13.90 52.30)"],
            route_type=700,
        )
        return await TransitOverlayService(session).route_geometry("S7")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    lons = [lon for lon, _ in _coords(overlay.geojson)]
    # The 700-bus lives at lon ~13.8-13.9; the dominant 109 spine stays west.
    assert max(lons) <= 13.5


def test_route_geometry_picks_longest_per_direction(async_db_url):
    """Two routes share a name and direction — a short-turn and the full line.
    Only the longest is drawn (drops the short-turn that caused loops)."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-x-short",
            short_name="X1",
            wkts=["LINESTRING(13.30 52.50, 13.31 52.50)"],
        )
        await _seed_line(
            session,
            route_id="r-x-long",
            short_name="X1",
            wkts=["LINESTRING(13.30 52.50, 13.45 52.50)"],
        )
        return await TransitOverlayService(session).route_geometry("X1")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    # One direction, one winner → a single LineString, not a MultiLineString.
    assert overlay.geojson["type"] == "LineString"
    lons = [lon for lon, _ in _coords(overlay.geojson)]
    assert max(lons) == 13.45  # the long line, not the 13.31 short-turn


def test_route_geometry_keeps_both_directions_longest_each(async_db_url):
    """Per direction, the longest shape wins — two directions → a
    MultiLineString of exactly two long components."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-y-long",
            short_name="Y1",
            wkts=[
                "LINESTRING(13.30 52.50, 13.45 52.50)",
                "LINESTRING(13.45 52.51, 13.30 52.51)",
            ],
        )
        await _seed_line(
            session,
            route_id="r-y-short",
            short_name="Y1",
            wkts=[
                "LINESTRING(13.30 52.50, 13.31 52.50)",
                "LINESTRING(13.31 52.51, 13.30 52.51)",
            ],
        )
        return await TransitOverlayService(session).route_geometry("Y1")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.geojson["type"] == "MultiLineString"
    assert len(overlay.geojson["coordinates"]) == 2
    # Both components reach 13.45 (the long shapes), neither is the short-turn.
    for line in overlay.geojson["coordinates"]:
        assert max(c[0] for c in line) == 13.45


def test_stations_restricted_to_shape(async_db_url):
    """A stop must claim the line AND lie near the drawn spine. A stop that
    claims the line but sits on a parallel street far from the shape is dropped
    (issue #30 — off-shape variant-pattern stops)."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-142",
            short_name="142",
            wkts=["LINESTRING(13.30 52.50, 13.40 52.50)"],
        )
        # ~55 m off the line → kept.
        await _seed_stop(
            session, stop_id="near", name="On Route", lon=13.35, lat=52.5005, lines=["142"]
        )
        # ~670 m off the line on a parallel street → dropped despite claiming 142.
        await _seed_stop(
            session, stop_id="far", name="Parallel St", lon=13.35, lat=52.5060, lines=["142"]
        )
        return await TransitOverlayService(session).route_geometry("142")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert {p.label for p in overlay.points} == {"On Route"}


def test_stations_snapped_onto_line(async_db_url):
    """A served station's dot is snapped onto the drawn spine, not left at its
    raw position — so it sits exactly on the line (issue #30 follow-up)."""

    async def body(session: AsyncSession):
        await _seed_line(
            session,
            route_id="r-m10",
            short_name="M10",
            wkts=["LINESTRING(13.30 52.50, 13.40 52.50)"],  # horizontal at lat 52.50
        )
        # ~55 m north of the line; claims M10.
        await _seed_stop(
            session, stop_id="off", name="Off Line", lon=13.35, lat=52.5005, lines=["M10"]
        )
        return await TransitOverlayService(session).route_geometry("M10")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    dot = next(p for p in overlay.points if p.label == "Off Line")
    # Snapped onto the line: lat pulled to 52.50, lon preserved.
    assert dot.lat == 52.5
    assert dot.lon == 13.35


def test_route_geometry_no_shape_returns_none(async_db_url):
    """A line known to `transit_routes` but with no shape rows can't be drawn —
    route_geometry returns None (the agent then just doesn't draw it)."""

    async def body(session: AsyncSession):
        await _seed_line(session, route_id="r-noshape", short_name="N1", wkts=[])
        return await TransitOverlayService(session).route_geometry("N1")

    assert _run(async_db_url, body) is None
