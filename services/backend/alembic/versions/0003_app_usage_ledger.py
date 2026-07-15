"""app schema: usage_ledger

Per-user LLM token ledger — the §3 budget store from llm-rate-limit.md. One
append-only row per completed agent run, written in `on_complete` from
`result.usage`. The per-user gate is a windowed `SUM(total_tokens)` over
`(user_id, created_at)`, which `ix_usage_ledger_user_created` serves index-only.

`user_id` CASCADEs (a deleted user has no budget to enforce). `conversation_id`
is nullable + `ON DELETE SET NULL` on purpose: usage is a fact about the user
that must OUTLIVE a conversation delete — otherwise deleting threads would reset
the budget.

Pure-schema migration: `upgrade`/`downgrade` round-trip cleanly (see the
migrations rule in CLAUDE.md).

Revision ID: 0003_app_usage_ledger
Revises: 0002_app_bookmarks
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_app_usage_ledger"
down_revision: str | Sequence[str] | None = "0002_app_bookmarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_ledger"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.users.id"],
            ondelete="CASCADE",
            name="fk_usage_ledger_user",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["app.conversations.id"],
            ondelete="SET NULL",
            name="fk_usage_ledger_conversation",
        ),
        schema="app",
    )
    op.create_index(
        "ix_usage_ledger_user_created",
        "usage_ledger",
        ["user_id", sa.text("created_at DESC")],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index("ix_usage_ledger_user_created", "usage_ledger", schema="app")
    op.drop_table("usage_ledger", schema="app")
