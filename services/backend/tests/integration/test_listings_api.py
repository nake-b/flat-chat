"""HTTP integration tests for `GET /api/listings/{id}`.

Same `ListingService.get(id)` powers both this route and the agent's
`open_listing` tool — `test_listing_service.py` covers the service
contract. This file covers the HTTP layer: status codes, body shape, and
the Cache-Control header that the frontend relies on for the 5-minute
detail-panel cache.

Approach: open one async engine + transaction, seed via the shared
factory, override `get_async_db` so the request handler resolves to the
same transaction-scoped session, then drive the FastAPI app via
`httpx.ASGITransport`. The lifespan handler is bypassed (we don't need
the Jina embedder or Phoenix for this route).
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import sqlalchemy as sa
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.core.database import get_async_db
from flat_chat.listings.models import Listing, ListingGeoContext
from flat_chat.main import app

from ..conftest import DB_REQUIRED
from ..fixtures.factories import gold_row as _gold_row
from ..fixtures.factories import listing_row as _listing_row

pytestmark = DB_REQUIRED


async def _run_http(
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body,
):
    """Open a transaction, seed, override the DB dep, run ``body(client)``,
    ROLLBACK. Mirrors the pattern in ``fixtures.factories`` but routes
    requests through the FastAPI app instead of straight to a service."""
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

                async def _override_db():
                    # Yield the same session every request resolves through —
                    # all writes happen inside the outer transaction and roll
                    # back on exit. NOT closed here; the outer block owns it.
                    yield session

                app.dependency_overrides[get_async_db] = _override_db
                try:
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as client:
                        return await body(client)
                finally:
                    app.dependency_overrides.pop(get_async_db, None)
                    await session.close()
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def _drive(async_url, seeds, body):
    return asyncio.run(_run_http(async_url, seeds, body))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_listing_200_returns_full_detail_with_cache_header(async_db_url):
    listing = _listing_row(
        title="Sunny corner flat",
        warm_rent_eur=1450.0,
        rooms=2.5,
        district="Kreuzberg",
    )
    gold = _gold_row(
        listing["id"],
        # noise_total_lden = scalar for search; noise_profile JSONB feeds
        # ListingDetail.noise. Set both so search + detail agree.
        noise_total_lden=58.0,
        noise_profile={"total_lden": 58.0},
        mss_profile={"status": "mixed", "dynamics": "improving"},
    )

    async def body(client):
        return await client.get(f"/api/listings/{listing['id']}")

    response = _drive(async_db_url, [(listing, gold)], body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(listing["id"])
    assert payload["title"] == "Sunny corner flat"
    assert payload["price_warm_eur"] == 1450.0
    assert payload["rooms"] == 2.5
    # Geo-context labels applied at projection time.
    assert payload["noise"] is not None
    assert payload["noise"]["label"] == "lively"  # 55 ≤ 58 < 65
    assert payload["mss"] is not None
    assert payload["mss"]["status"] == "mixed"
    # The frontend's 5-minute browser cache hinges on this exact header.
    assert response.headers["cache-control"] == "public, max-age=300"


def test_get_listing_404_for_unknown_uuid(async_db_url):
    random_id = uuid.uuid4()

    async def body(client):
        return await client.get(f"/api/listings/{random_id}")

    response = _drive(async_db_url, [], body)
    assert response.status_code == 404
    assert response.json() == {"detail": "listing not found"}


def test_get_listing_404_for_invalid_uuid(async_db_url):
    """Non-UUID id → ListingService returns None → route raises 404.

    The route declares `listing_id: str`, so FastAPI doesn't reject at
    the parsing layer. The not-a-uuid path lives inside the service.
    """

    async def body(client):
        return await client.get("/api/listings/not-a-uuid")

    response = _drive(async_db_url, [], body)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/listings?ids=…&view=card — the batch (lazy-hydrate) read
# ---------------------------------------------------------------------------


def test_list_listings_batch_returns_cards_in_request_order(async_db_url):
    l1 = _listing_row(title="One", warm_rent_eur=1000.0)
    l2 = _listing_row(title="Two", warm_rent_eur=2000.0)
    seeds = [(l1, _gold_row(l1["id"])), (l2, _gold_row(l2["id"]))]

    async def body(client):
        return await client.get(
            "/api/listings",
            params=[("ids", str(l2["id"])), ("ids", str(l1["id"])), ("view", "card")],
        )

    response = _drive(async_db_url, seeds, body)
    assert response.status_code == 200
    payload = response.json()
    # Cards come back in request order (l2 before l1).
    assert [c["id"] for c in payload] == [str(l2["id"]), str(l1["id"])]
    assert payload[0]["title"] == "Two"
    assert response.headers["cache-control"] == "public, max-age=300"


def test_list_listings_view_detail_is_rejected(async_db_url):
    async def body(client):
        return await client.get("/api/listings", params={"view": "detail"})

    response = _drive(async_db_url, [], body)
    assert response.status_code == 422


def test_list_listings_over_cap_is_rejected(async_db_url):
    too_many = [("ids", str(uuid.uuid4())) for _ in range(101)]

    async def body(client):
        return await client.get("/api/listings", params=too_many)

    response = _drive(async_db_url, [], body)
    assert response.status_code == 422
