"""Integration tests for `PlaceService.locate` (the `locate_place` backend).

`PlaceService` reads the ingestion-owned `world.named_places` VIEW and
resolves a free-text place name to candidate `place_ref` tokens via the
pg_trgm `%` operator + `similarity()` ranking. These tests EXECUTE against
Postgres — the `%` operator and the GIN trigram indexes exist only there
(Phase 1's 0007 migration), so a compile-only test would miss an operator
or index regression.

Each test seeds a row in a base table behind the view (`landmarks`,
`parks`), runs `locate`, and asserts the candidate comes back with the
view-composed `place_ref` and a centroid lat/lon. Rolled back per test.
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.search.places import PlaceService

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


def test_locate_resolves_by_trigram_similarity(async_db_url):
    """A seeded landmark is found by a fuzzy / partial name; the view
    composes `place_ref` as `landmark:<id>` and lat/lon are the centroid."""

    async def body(session: AsyncSession):
        landmark_id = await session.scalar(
            sa.text(
                """
                INSERT INTO world.landmarks (name, source, category, geom)
                VALUES (
                    'Brandenburger Tor', 'osm', 'monument',
                    ST_SetSRID(ST_MakePoint(13.3777, 52.5163), 4326)
                )
                RETURNING id
                """
            )
        )
        # Fuzzy / partial query — trigram match, not exact equality.
        candidates = await PlaceService(session).locate("Brandenburg Tor")
        return landmark_id, candidates

    landmark_id, candidates = _run(async_db_url, body)
    assert candidates, "expected at least one trigram match"
    top = candidates[0]
    assert top.place_ref == f"landmark:{landmark_id}"
    assert top.kind == "landmark"
    assert top.name == "Brandenburger Tor"
    # Centroid of a point is the point itself.
    assert top.lat is not None and abs(top.lat - 52.5163) < 1e-3
    assert top.lon is not None and abs(top.lon - 13.3777) < 1e-3


def test_locate_returns_centroid_for_extended_geometry(async_db_url):
    """A LINESTRING (e.g. the Spree) still yields a single display point via
    ST_Centroid, and resolves through the `landmark:` view branch."""

    async def body(session: AsyncSession):
        await session.execute(
            sa.text(
                """
                INSERT INTO world.landmarks (name, source, category, geom)
                VALUES (
                    'Spree', 'osm', 'river',
                    ST_SetSRID(
                        ST_GeomFromText('LINESTRING(13.30 52.50, 13.50 52.50)'),
                        4326
                    )
                )
                """
            )
        )
        return await PlaceService(session).locate("Spree")

    candidates = _run(async_db_url, body)
    assert candidates
    top = candidates[0]
    assert top.name == "Spree"
    # Centroid of the E-W line sits at ~lon 13.40, lat 52.50.
    assert top.lat is not None and abs(top.lat - 52.50) < 1e-3
    assert top.lon is not None and abs(top.lon - 13.40) < 1e-2


def test_locate_empty_query_returns_empty(async_db_url):
    async def body(session: AsyncSession):
        return await PlaceService(session).locate("   ")

    assert _run(async_db_url, body) == []


def test_locate_no_match_returns_empty(async_db_url):
    async def body(session: AsyncSession):
        return await PlaceService(session).locate("zzzzx-no-such-place-qqqq")

    assert _run(async_db_url, body) == []
