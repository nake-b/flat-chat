"""ORM models for durable conversations — backend-owned + migrated (`app` schema).

These back `DbSessionStore` (chat/sessions.py). The split mirrors the in-memory
`ChatSession`:

  - `Conversation` — one thread. Its `id` doubles as the AG-UI `thread_id`, so the
    client supplies it (no server-side default). Scoped to a `User`.
  - `Message`   — one serialized Pydantic-AI `ModelMessage` per row, ordered by `seq`,
    append-only. We store the opaque `ModelMessagesTypeAdapter` form in `content`
    (jsonb) — we never query inside a message, we reload the list to rehydrate the
    agent. `kind` is the message-level discriminator (`request`/`response`), kept for
    cheap inspection — NOT the part-level role (user/assistant lives inside `parts`).
  - `SessionStateRow` — the latest `SessionState` snapshot (map markers / cards /
    active listing), ONE row per conversation, overwritten per turn. This is derived
    presentation state, rebuildable from the messages — so overwrite (not append-log)
    is correct. It is the cross-reload recovery primitive served by `GET /…/state`.

`title` / `archived_at` are present but unused this PR (no sidebar/titling/archive UX
yet) — cheap to carry so the future conversation-list feature is a pure additive change.

Decision doc: session-persistence.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base


class Conversation(Base):
    """One conversation thread. `id` == the AG-UI `thread_id`."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        {"schema": "app"},
    )

    # Client/AG-UI supplies the id (it is the thread_id) — no server default.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Message(Base):
    """One serialized Pydantic-AI ModelMessage, ordered by `seq` per conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        # Guards ordering integrity AND turns a concurrent double-append into a
        # loud IntegrityError instead of silent duplicate rows (see R1 in the plan).
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
        Index("ix_messages_conversation_seq", "conversation_id", "seq"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class SessionStateRow(Base):
    """Latest SessionState snapshot per conversation. One row, overwritten per turn."""

    __tablename__ = "session_state"
    __table_args__ = ({"schema": "app"},)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UsageLedger(Base):
    """One row per completed agent run — the per-user LLM token ledger.

    Append-only accounting written in `on_complete` from `result.usage`. The
    per-user budget gate (`UsageService.spent_last_24h`) is a windowed
    `SUM(total_tokens)` over `(user_id, created_at)`, which the composite index
    serves index-only. `total_tokens` (= input + output; Pydantic AI excludes
    cache reads/writes from its total) is the budget currency AND the per-run
    `total_tokens_limit` currency, so the two stay consistent. The cache-read /
    cache-write columns are stored but NOT budgeted — kept so a future
    cost-weighted budget (cache reads are ~10× cheaper on Anthropic) can be
    computed without a backfill.

    `conversation_id` is nullable + `ON DELETE SET NULL`: usage is a fact about
    the user that must survive a conversation delete (otherwise a user could
    reset their budget by deleting threads). `user_id` CASCADEs — a deleted user
    has no budget to enforce.

    Decision doc: llm-rate-limit.md.
    """

    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_user_created", "user_id", text("created_at DESC")),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="SET NULL"),
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
