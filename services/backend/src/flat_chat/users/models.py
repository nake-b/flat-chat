"""ORM model for the users domain — owned + migrated by the backend (`app` schema).

Minimal today: a user is just an id + timestamps. There is NO auth yet — a single
hardcoded DUMMY user is upserted on demand (see `DbSessionStore.create`), reached
through the `get_user_id()` seam in `core/dependencies.py`.

The upgrade path is **claim-in-place**: when real accounts land, add nullable
`email` / `password_hash` / `auth_provider` / `claimed_at` columns and UPDATE the
existing row on signup — the primary key never changes, so conversations (and later
bookmarks) keep their foreign keys without re-keying. See session-persistence.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base

# Stage-1 identity seam: a single hardcoded user until real auth lands. Materialized
# on demand (DbSessionStore.create upserts it) and returned by `get_user_id()`. Stages
# 2 (anonymous cookie) and 3 (JWT sub) swap only that dependency — never this id's role
# as a foreign-key target. See session-persistence.md.
DUMMY_USER_ID = "00000000-0000-0000-0000-000000000001"


class User(Base):
    """An app user. Anonymous/dummy today; the seam for real auth later."""

    __tablename__ = "users"
    __table_args__ = ({"schema": "app"},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
