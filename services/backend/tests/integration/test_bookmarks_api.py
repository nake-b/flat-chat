"""HTTP integration tests for the bookmarks endpoints.

  POST   /api/bookmarks/{listing_id}
  DELETE /api/bookmarks/{listing_id}
  GET    /api/bookmarks/ids
  GET    /api/bookmarks

Pattern mirrors `test_listings_api.py`: one async engine + transaction,
seed via the shared factory, override `get_async_db` so the route resolves
to the test session, override `get_user_id`, drive the FastAPI app via
``ASGITransport``, ROLLBACK on exit. The BookmarkService commits explicitly
— the default `join_transaction_mode="conditional_savepoint"` on
``AsyncSession`` turns those commits into savepoint releases inside the
outer transaction, so everything still rolls back cleanly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.core.database import get_async_db
from flat_chat.core.dependencies import get_user_id
from flat_chat.listings.bookmarks import Bookmark
from flat_chat.listings.models import Listing
from flat_chat.main import app
from flat_chat.users.models import User

from ..conftest import DB_REQUIRED, ensure_app_users
from ..fixtures.factories import gold_row as _gold_row
from ..fixtures.factories import listing_row as _listing_row

pytestmark = DB_REQUIRED

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"


async def _run_http(
    async_url: str,
    seeds: list[tuple[dict, dict | None]],
    body,
    request_user: str = USER_A,
):
    """Open a transaction, seed, override DB + user deps, run ``body(client, session)``,
    ROLLBACK. The same ``AsyncSession`` is yielded to every request so post-write
    assertions can query the same scope without re-reading from a separate session."""
    engine = create_async_engine(async_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                # Auth columns are NOT NULL and `BookmarkService` no longer
                # fabricates users — seed the real user rows the FK needs.
                await ensure_app_users(conn, USER_A, USER_B)
                for listing_kwargs, gold_kwargs in seeds:
                    await conn.execute(sa.insert(Listing).values(**listing_kwargs))
                    if gold_kwargs is not None:
                        from flat_chat.listings.models import ListingGeoContext

                        await conn.execute(
                            sa.insert(ListingGeoContext).values(**gold_kwargs)
                        )

                session = AsyncSession(bind=conn, expire_on_commit=False)

                async def _override_db():
                    yield session

                app.dependency_overrides[get_async_db] = _override_db
                app.dependency_overrides[get_user_id] = lambda: request_user
                try:
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as client:
                        return await body(client, session)
                finally:
                    app.dependency_overrides.pop(get_async_db, None)
                    app.dependency_overrides.pop(get_user_id, None)
                    await session.close()
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


def _drive(async_url, seeds, body, request_user=USER_A):
    return asyncio.run(_run_http(async_url, seeds, body, request_user))


# ---------------------------------------------------------------------------
# POST /api/bookmarks/{listing_id}
# ---------------------------------------------------------------------------


def test_add_bookmark_creates_row_and_appears_in_ids(async_db_url):
    listing = _listing_row(title="One")

    async def body(client, _session):
        post = await client.post(f"/api/bookmarks/{listing['id']}")
        ids = await client.get("/api/bookmarks/ids")
        return post, ids

    post, ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body)
    assert post.status_code == 200
    assert post.json() == {"status": "ok"}
    assert ids.status_code == 200
    assert ids.json() == [str(listing["id"])]


def test_add_bookmark_is_idempotent(async_db_url):
    """POSTing twice returns 200 both times and creates exactly one row."""
    listing = _listing_row()

    async def body(client, session):
        r1 = await client.post(f"/api/bookmarks/{listing['id']}")
        r2 = await client.post(f"/api/bookmarks/{listing['id']}")
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Bookmark)
            .where(Bookmark.listing_id == listing["id"])
        )
        return r1, r2, count

    r1, r2, count = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert count == 1


def test_add_bookmark_for_authenticated_user(async_db_url):
    """An authenticated user can bookmark — the FK resolves against their real
    `app.users` row (no on-demand user fabrication post-auth)."""
    listing = _listing_row()

    async def body(client, session):
        post = await client.post(f"/api/bookmarks/{listing['id']}")
        user_present = await session.scalar(
            sa.select(sa.func.count())
            .select_from(User)
            .where(User.id == uuid.UUID(USER_A))
        )
        return post, user_present

    post, user_present = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body
    )
    assert post.status_code == 200
    assert user_present == 1


def test_add_bookmark_invalid_listing_id_is_422(async_db_url):
    async def body(client, _session):
        return await client.post("/api/bookmarks/not-a-uuid")

    resp = _drive(async_db_url, [], body)
    assert resp.status_code == 422


def test_add_bookmark_unknown_listing_is_404_not_500(async_db_url):
    """A well-formed UUID with no matching listing → 404 (FK can't resolve),
    not a 500 from the raw IntegrityError. No bookmark row is created."""
    unknown = uuid.uuid4()

    async def body(client, session):
        resp = await client.post(f"/api/bookmarks/{unknown}")
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Bookmark)
            .where(Bookmark.listing_id == unknown)
        )
        return resp, count

    resp, count = _drive(async_db_url, [], body)
    assert resp.status_code == 404
    assert count == 0


# ---------------------------------------------------------------------------
# DELETE /api/bookmarks/{listing_id}
# ---------------------------------------------------------------------------


def test_remove_bookmark_returns_204_and_drops_id(async_db_url):
    listing = _listing_row()

    async def body(client, _session):
        await client.post(f"/api/bookmarks/{listing['id']}")
        delete = await client.delete(f"/api/bookmarks/{listing['id']}")
        ids = await client.get("/api/bookmarks/ids")
        return delete, ids

    delete, ids = _drive(async_db_url, [(listing, _gold_row(listing["id"]))], body)
    assert delete.status_code == 204
    assert ids.json() == []


def test_remove_bookmark_when_absent_still_204(async_db_url):
    """Optimistic UI shouldn't see 404 noise on a double-delete."""

    async def body(client, _session):
        return await client.delete(f"/api/bookmarks/{uuid.uuid4()}")

    resp = _drive(async_db_url, [], body)
    assert resp.status_code == 204


def test_remove_bookmark_invalid_listing_id_is_422(async_db_url):
    async def body(client, _session):
        return await client.delete("/api/bookmarks/not-a-uuid")

    resp = _drive(async_db_url, [], body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/bookmarks  +  GET /api/bookmarks/ids
# ---------------------------------------------------------------------------


def test_list_bookmarks_empty_returns_empty_array(async_db_url):
    async def body(client, _session):
        ids = await client.get("/api/bookmarks/ids")
        cards = await client.get("/api/bookmarks")
        return ids, cards

    ids, cards = _drive(async_db_url, [], body)
    assert ids.status_code == 200 and ids.json() == []
    assert cards.status_code == 200 and cards.json() == []


def test_list_bookmarks_returns_cards_newest_first(async_db_url):
    """ORDER BY created_at DESC — newest bookmark first.

    Inside a single transaction Postgres's `now()` is frozen to the transaction
    start (server-default `created_at` would tie), so the test seeds explicit
    timestamps via SQL to exercise the ORDER BY independently.
    """
    older = _listing_row(title="Older one", warm_rent_eur=900.0)
    newer = _listing_row(title="Newer one", warm_rent_eur=1500.0)
    seeds = [
        (older, _gold_row(older["id"])),
        (newer, _gold_row(newer["id"])),
    ]
    t0 = datetime.now(tz=UTC)

    async def body(client, session):
        # POST then back-date the row so the subsequent POST has a higher
        # `created_at`. Setting both rows after the fact avoids race with the
        # service's commit semantics.
        await client.post(f"/api/bookmarks/{older['id']}")
        await session.execute(
            sa.text(
                "UPDATE app.bookmarks SET created_at = :ts WHERE listing_id = :lid"
            ),
            {"ts": t0 - timedelta(hours=1), "lid": older["id"]},
        )
        await client.post(f"/api/bookmarks/{newer['id']}")
        await session.execute(
            sa.text(
                "UPDATE app.bookmarks SET created_at = :ts WHERE listing_id = :lid"
            ),
            {"ts": t0, "lid": newer["id"]},
        )
        await session.commit()
        ids = await client.get("/api/bookmarks/ids")
        cards = await client.get("/api/bookmarks")
        return ids, cards

    ids, cards = _drive(async_db_url, seeds, body)
    assert ids.json() == [str(newer["id"]), str(older["id"])]
    assert [c["id"] for c in cards.json()] == [str(newer["id"]), str(older["id"])]
    assert cards.json()[0]["title"] == "Newer one"


def test_user_isolation(async_db_url):
    """USER_A's bookmark is invisible to USER_B."""
    listing = _listing_row()

    async def body_a(client, _session):
        return await client.post(f"/api/bookmarks/{listing['id']}")

    async def body_b(client, _session):
        return await client.get("/api/bookmarks/ids")

    # Within a single drive call both calls run on the same transaction; switch
    # `request_user` mid-flight via a single body that exercises both perspectives.
    async def combined(client, _session):
        # First, USER_A bookmarks.
        app.dependency_overrides[get_user_id] = lambda: USER_A
        a_post = await client.post(f"/api/bookmarks/{listing['id']}")
        # Then, swap to USER_B and list.
        app.dependency_overrides[get_user_id] = lambda: USER_B
        b_ids = await client.get("/api/bookmarks/ids")
        b_cards = await client.get("/api/bookmarks")
        # Finally, swap back to USER_A and confirm still visible.
        app.dependency_overrides[get_user_id] = lambda: USER_A
        a_ids = await client.get("/api/bookmarks/ids")
        return a_post, b_ids, b_cards, a_ids

    a_post, b_ids, b_cards, a_ids = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], combined
    )
    assert a_post.status_code == 200
    assert b_ids.json() == []
    assert b_cards.json() == []
    assert a_ids.json() == [str(listing["id"])]


def test_cascade_on_listing_delete(async_db_url):
    """Deleting a listing from world.listings sweeps every bookmark pointing at it.

    The CASCADE FK on `app.bookmarks.listing_id` is the guarantee that protects
    against stale references — exercise it end-to-end against postgres so a
    constraint change in a future migration trips this test instantly.
    """
    listing = _listing_row()

    async def body(client, session):
        await client.post(f"/api/bookmarks/{listing['id']}")
        pre = await client.get("/api/bookmarks/ids")
        # Delete the listing directly via SQL. The bookmark row should go too.
        await session.execute(sa.delete(Listing).where(Listing.id == listing["id"]))
        await session.commit()
        post = await client.get("/api/bookmarks/ids")
        post_cards = await client.get("/api/bookmarks")
        return pre, post, post_cards

    pre, post, post_cards = _drive(
        async_db_url, [(listing, _gold_row(listing["id"]))], body
    )
    assert pre.json() == [str(listing["id"])]
    assert post.json() == []
    assert post_cards.json() == []
