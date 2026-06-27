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
from sqlalchemy.ext.asyncio import AsyncSession

from flat_chat.listings.bookmarks_models import Bookmark
from flat_chat.listings.context import ListingCard
from flat_chat.listings.service import ListingService
from flat_chat.users.models import User


class BookmarkService:
    def __init__(self, db: AsyncSession, listing_service: ListingService) -> None:
        self.db = db
        self.listing_service = listing_service

    async def add(self, user_id: str, listing_id: str) -> None:
        """Upsert a bookmark. Idempotent — re-adding is a no-op.

        Also upserts the user row in the same transaction. `DbSessionStore.create`
        lazily materialises the dummy user on the first conversation, but a
        brand-new user may bookmark before chatting; without the upsert here
        the FK would fail.
        """
        user_uuid = uuid.UUID(user_id)
        listing_uuid = uuid.UUID(listing_id)
        await self.db.execute(
            pg_insert(User)
            .values(id=user_uuid)
            .on_conflict_do_nothing(index_elements=[User.id])
        )
        await self.db.execute(
            pg_insert(Bookmark)
            .values(user_id=user_uuid, listing_id=listing_uuid)
            .on_conflict_do_nothing(
                index_elements=[Bookmark.user_id, Bookmark.listing_id]
            )
        )
        await self.db.commit()

    async def remove(self, user_id: str, listing_id: str) -> None:
        """Delete a bookmark. Idempotent — a no-op if the row didn't exist.

        The route is 204 either way; the frontend's optimistic UI shouldn't
        see 404 noise on a double-delete.
        """
        user_uuid = uuid.UUID(user_id)
        listing_uuid = uuid.UUID(listing_id)
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
