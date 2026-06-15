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
from datetime import datetime, timezone
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.listings.models import Listing, ListingGeoContext
from flat_chat.search.service import SearchService

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def listing_row(**overrides) -> dict:
    """Minimal valid Listing row. Tests override fields they need to filter on."""
    return {
        "id": uuid.uuid4(),
        "source_name": "test",
        "external_id": str(uuid.uuid4()),
        "scraped_at": _now(),
        **overrides,
    }


def gold_row(listing_id: uuid.UUID, **overrides) -> dict:
    """Minimal valid ListingGeoContext row."""
    return {"listing_id": listing_id, **overrides}


async def _seed_and_run(
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Open a transactional async session, seed rows, run ``body``, ROLLBACK."""
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
                session = AsyncSession(bind=conn, expire_on_commit=False)
                try:
                    return await body(session)
                finally:
                    await session.close()
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def drive_search(
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[SearchService], Awaitable[T]],
) -> T:
    """Sync wrapper — hands ``SearchService`` to ``body``.

    Bridge for the existing test_search_service.py call sites; new
    listing-service / API tests should use ``with_session`` instead.
    """

    async def _wrapped(session: AsyncSession) -> T:
        return await body(SearchService(session))

    return asyncio.run(_seed_and_run(async_url, seeds, _wrapped))


def with_session(
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Sync wrapper — hands a raw ``AsyncSession`` to ``body``."""
    return asyncio.run(_seed_and_run(async_url, seeds, body))
