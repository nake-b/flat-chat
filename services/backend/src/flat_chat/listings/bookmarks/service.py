"""BookmarkService — per-user saved listings.

CRUD over `app.bookmarks` + tier-2 card hydration via the shared
`ListingService.get_cards`. HTTP-only (no agent path) — bookmarks are a
frontend concern, not an agent capability. See
`agent-compound-docs/decisions/agent-vs-http-data-flow.md`.

Idempotent `add` (`ON CONFLICT DO NOTHING`) and `remove` (DELETE returns
whether a row was actually deleted; the route maps both cases to 204) so
the frontend's optimistic UI never sees 404/409 noise on double-clicks.

Mirrors `ListingService`'s shape: `db` on the constructor, `user_id` as a
per-call argument so the same service instance can serve any user in a
multi-tenant request scope.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.bookmarks.models import Bookmark
from flat_chat.listings.context import ListingCard
from flat_chat.listings.service import ListingService


class UnknownListingError(Exception):
    """Raised by `BookmarkService.add` when the listing FK can't be satisfied.

    A well-formed UUID that points at no `world.listings` row trips the
    cross-schema foreign key. The route maps this to a 404 (the listing doesn't
    exist) rather than letting the raw IntegrityError surface as a 500.
    """


class BookmarkService:
    def __init__(self, db: AsyncSession, listing_service: ListingService) -> None:
        self.db = db
        self.listing_service = listing_service

    async def add(self, user_id: str, listing_id: str | uuid.UUID) -> None:
        """Upsert a bookmark. Idempotent — re-adding is a no-op.

        The caller is always an authenticated user (the route resolves
        `get_user_id` → `current_active_user`), so the `app.users` row already
        exists and the FK is satisfied — no user upsert needed here.

        `listing_id` accepts a str or UUID (the route passes the pre-validated
        `UUID` from `valid_listing_id`); mirrors `ListingService`'s shape.

        Raises `UnknownListingError` if the listing FK can't be satisfied (a
        well-formed but non-existent listing id) — the route turns that into a
        404 instead of a 500. `on_conflict_do_nothing` covers the PK collision
        (re-adding), but NOT the FK, so the insert can still raise.
        """
        user_uuid = uuid.UUID(user_id)
        listing_uuid = uuid.UUID(str(listing_id))
        try:
            await self.db.execute(
                pg_insert(Bookmark)
                .values(user_id=user_uuid, listing_id=listing_uuid)
                .on_conflict_do_nothing(index_elements=["user_id", "listing_id"])
            )
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise UnknownListingError(str(listing_uuid)) from exc

    async def remove(self, user_id: str, listing_id: str | uuid.UUID) -> None:
        """Delete a bookmark. Idempotent — a no-op if the row didn't exist.

        The route is 204 either way; the frontend's optimistic UI shouldn't
        see 404 noise on a double-delete. `listing_id` accepts a str or UUID.
        """
        user_uuid = uuid.UUID(user_id)
        listing_uuid = uuid.UUID(str(listing_id))
        await self.db.execute(
            delete(Bookmark)
            .where(Bookmark.user_id == user_uuid)
            .where(Bookmark.listing_id == listing_uuid)
        )
        await self.db.commit()

    async def list_ids(self, user_id: str) -> list[str]:
        """All bookmarked listing ids for the user, newest first.

        Fast path: no join against `world.listings`, no tier-2 card hydration.
        Powers the frontend's mount-time hydration of star-state on every card.
        """
        user_uuid = uuid.UUID(user_id)
        stmt = (
            select(Bookmark.listing_id)
            .where(Bookmark.user_id == user_uuid)
            .order_by(Bookmark.created_at.desc())
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [str(r) for r in rows]

    async def list_cards(self, user_id: str) -> list[ListingCard]:
        """Hydrated tier-2 cards for the user's bookmarks, newest first.

        Two queries by design: list ids ordered by `created_at DESC`, then
        hand them to `ListingService.get_cards` which preserves caller-supplied
        order. A listing deleted since the bookmark was added simply doesn't
        come back (the CASCADE will eventually catch the row, but a same-
        transaction window is possible).
        """
        ids = await self.list_ids(user_id)
        if not ids:
            return []
        return await self.listing_service.get_cards(ids)
