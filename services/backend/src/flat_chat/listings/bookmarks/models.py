"""ORM model for the bookmarks domain — backend-owned + migrated (`app` schema).

A per-user, per-listing tuple. Composite PK `(user_id, listing_id)` makes
idempotent `ON CONFLICT DO NOTHING` upserts trivial.

Both FKs CASCADE: deleting a user wipes their bookmarks; deleting a listing
(silver-side via ingestion) wipes every bookmark pointing at it. No
relationships back-populated — `User` is only used as an FK target today
and the service queries `Bookmark.user_id == ...` directly. Same shape
as `Conversation`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base


class Bookmark(Base):
    """A user's bookmark on one listing. Composite PK (user_id, listing_id)."""

    __tablename__ = "bookmarks"
    __table_args__ = (
        Index(
            "ix_bookmarks_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        {"schema": "app"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
