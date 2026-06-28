"""ORM model for the users domain — owned + migrated by the backend (`app` schema).

Real password auth via **fastapi-users** (see `users/auth.py`). The columns
`email` / `hashed_password` / `is_active` / `is_superuser` / `is_verified` are the
fastapi-users contract; `SQLAlchemyUserDatabase` reads them by attribute, so this
model satisfies the adapter WITHOUT inheriting its mixin. We define it by hand to
keep the existing primary key (`gen_random_uuid()` server default) so the `0001`
migration's `id` column is unchanged — no FK re-keying of conversations.

`email` / `hashed_password` are **NOT NULL** — every user is a real account
(created by the seed script, `scripts/seed_users.py`). There are no dummy /
placeholder rows:
`DbSessionStore.create` no longer fabricates a user, so a conversation can only
reference a user that already exists. `DUMMY_USER_ID` survives solely as a fixed id
for `InMemorySessionStore` unit tests (no DB).

NOTE: migration `0002` adds these NOT-NULL columns with no default, so it must run
against an EMPTY `app.users` table (a fresh / refreshed dev DB). See AUTH.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base

# Fixed id used only by InMemorySessionStore unit tests (no DB, no auth). The live
# request path resolves the real authenticated user via `get_user_id()`.
DUMMY_USER_ID = "00000000-0000-0000-0000-000000000001"


class User(Base):
    """An app user. fastapi-users-compatible by attribute contract, not inheritance.

    The fastapi-users contract columns use the same `if TYPE_CHECKING` split the
    library's own `SQLAlchemyBaseUserTable` mixin uses: the type checker sees plain
    `str` / `bool` annotations (so `User` satisfies fastapi-users' `UserProtocol`),
    while at runtime they're real SQLAlchemy `mapped_column`s. We do this by hand
    rather than inheriting the mixin so we keep the `gen_random_uuid()` server
    default on the PK from migration `0001` (the mixin would re-key it).
    """

    __tablename__ = "users"
    __table_args__ = ({"schema": "app"},)

    if TYPE_CHECKING:
        # Plain types for the static checker (matches fastapi-users' UserProtocol).
        id: uuid.UUID
        email: str
        hashed_password: str
        is_active: bool
        is_superuser: bool
        is_verified: bool
    else:
        id = mapped_column(
            UUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        )
        email = mapped_column(
            String(length=320), unique=True, index=True, nullable=False
        )
        hashed_password = mapped_column(String(length=1024), nullable=False)
        is_active = mapped_column(Boolean, nullable=False, server_default=text("true"))
        is_superuser = mapped_column(
            Boolean, nullable=False, server_default=text("false")
        )
        is_verified = mapped_column(
            Boolean, nullable=False, server_default=text("false")
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
