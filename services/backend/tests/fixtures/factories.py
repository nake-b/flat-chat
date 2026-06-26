"""Shared seed helpers + async-transaction driver for integration tests.

Lifted out of ``tests/integration/test_search_service.py`` so the other
integration suites (listings, search-noise-radius, etc.) reuse the same
ORM-backed seed shapes and the same rollback-per-test harness.

Conventions match the existing test_search_service.py file:
  - ``listing_row(**overrides)`` / ``gold_row(listing_id, **overrides)``
    return ORM kwargs dicts with sensible defaults.
  - ``drive_search(async_url, seeds, body)`` runs ``body(SearchService)``
    inside a transaction that ROLLBACKs on exit.
  - ``with_session(async_url, seeds, body)`` is the same but hands the
    plain ``AsyncSession`` to the body — for services other than
    SearchService.
  - Async via ``asyncio.run`` (no pytest-asyncio); matches the rest of
    the suite.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.listings.models import (
    Listing,
    ListingGeoContext,
    ListingNearbyHospital,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
)
from flat_chat.search.service import SearchService


def _now() -> datetime:
    return datetime.now(tz=UTC)


def listing_row(**overrides) -> dict:
    """Minimal valid Listing row. Tests override fields they need to filter on.

    Defaults include Berlin-centre coordinates: `SearchService` only projects
    listings with non-null lat/lng into markers (you can't plot a coordinate-
    less listing), so a seed without coords would never appear in search
    results. Tests that specifically exercise null-coordinate behaviour
    override `latitude`/`longitude` to None.
    """
    return {
        "id": uuid.uuid4(),
        "source_name": "test",
        "external_id": str(uuid.uuid4()),
        "scraped_at": _now(),
        "latitude": 52.52,
        "longitude": 13.405,
        **overrides,
    }


def gold_row(listing_id: uuid.UUID, **overrides) -> dict:
    """Minimal valid ListingGeoContext row."""
    return {"listing_id": listing_id, **overrides}


# ---------------------------------------------------------------------------
# Junction-table row helpers — one per POI family. Each returns a kwargs
# dict for the matching ORM model. Pass them via `drive_search(...,
# junctions=[(Model, kwargs), ...])` to seed the per-listing neighbour
# rows the search/listing service filters/joins on.
# ---------------------------------------------------------------------------


def nearby_transit_row(
    listing_id: uuid.UUID,
    stop_id: str | None = None,
    distance_m: int = 200,
    modes: list[int] | None = None,
    lines: list[str] | None = None,
    name: str | None = "U Test",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "stop_id": stop_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "modes": modes if modes is not None else [400],
        "lines": lines if lines is not None else ["U1"],
        "name": name,
        "rank": rank,
    }


def nearby_school_row(
    listing_id: uuid.UUID,
    school_id: str | None = None,
    distance_m: int = 400,
    school_type: str | None = "Grundschule",
    name: str | None = "GS Test",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "school_id": school_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "school_type": school_type,
        "name": name,
        "rank": rank,
    }


def nearby_hospital_row(
    listing_id: uuid.UUID,
    hospital_id: str | None = None,
    distance_m: int = 1500,
    tier: str | None = "plan_hospital",
    name: str | None = "Charité",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "hospital_id": hospital_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "tier": tier,
        "name": name,
        "rank": rank,
    }


def nearby_park_row(
    listing_id: uuid.UUID,
    park_id: str | None = None,
    distance_m: int = 250,
    object_type: str | None = "Volkspark",
    name: str | None = "Görlitzer Park",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "park_id": park_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "object_type": object_type,
        "name": name,
        "rank": rank,
    }


def nearby_playground_row(
    listing_id: uuid.UUID,
    playground_id: str | None = None,
    distance_m: int = 300,
    name: str | None = "Spielplatz Test",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "playground_id": playground_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "name": name,
        "rank": rank,
    }


def nearby_water_row(
    listing_id: uuid.UUID,
    water_id: str | None = None,
    distance_m: int = 800,
    water_kind: str | None = "Stehendes Gewässer",
    name: str | None = "Landwehrkanal",
    rank: int = 1,
) -> dict:
    return {
        "listing_id": listing_id,
        "water_id": water_id or str(uuid.uuid4()),
        "distance_m": distance_m,
        "water_kind": water_kind,
        "name": name,
        "rank": rank,
    }


# Map model class → row helper name for cleaner test seeds.
JUNCTION_MODELS = {
    "transit": ListingNearbyTransit,
    "schools": ListingNearbySchool,
    "hospitals": ListingNearbyHospital,
    "parks": ListingNearbyPark,
    "playgrounds": ListingNearbyPlayground,
    "water": ListingNearbyWater,
}


async def _seed_and_run[T](
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[AsyncSession], Awaitable[T]],
    junctions: list[tuple[type, dict]] | None = None,
) -> T:
    """Open a transactional async session, seed rows, run ``body``, ROLLBACK.

    ``junctions`` is an optional list of ``(Model, row_kwargs)`` pairs for
    seeding junction-table rows. Models live in
    ``flat_chat.listings.models`` (``ListingNearbyTransit``, etc.).
    """
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                for listing_kwargs, gold_kwargs in seeds:
                    await conn.execute(sa.insert(Listing).values(**listing_kwargs))
                    if gold_kwargs is not None:
                        await conn.execute(
                            sa.insert(ListingGeoContext).values(**gold_kwargs)
                        )
                for model, row_kwargs in junctions or []:
                    await conn.execute(sa.insert(model).values(**row_kwargs))
                session = AsyncSession(bind=conn, expire_on_commit=False)
                try:
                    return await body(session)
                finally:
                    await session.close()
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive_search[T](
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[SearchService], Awaitable[T]],
    junctions: list[tuple[type, dict]] | None = None,
) -> T:
    """Sync wrapper — hands ``SearchService`` to ``body``.

    Optional ``junctions`` lets the test pre-seed neighbour-table rows
    (``nearby_transit_row(...)`` and friends).
    """

    async def _wrapped(session: AsyncSession) -> T:
        return await body(SearchService(session))

    return asyncio.run(_seed_and_run(async_url, seeds, _wrapped, junctions))


def with_session[T](
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[AsyncSession], Awaitable[T]],
    junctions: list[tuple[type, dict]] | None = None,
) -> T:
    """Sync wrapper — hands a raw ``AsyncSession`` to ``body``."""
    return asyncio.run(_seed_and_run(async_url, seeds, body, junctions))
