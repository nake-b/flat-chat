"""app schema: users, conversations, messages, session_state

Durable users + session persistence. Pure schema — no data seed (the dummy user
is upserted on demand in DbSessionStore.create), so the round-trip stays clean.

The `app` schema + extensions are created by the postgres bootstrap
(services/postgres/init/ on a fresh volume, ./scripts/bootstrap-schemas.sh on an
existing one), not here — mirroring the world-schema 0001. See
schema-ownership-split.md and session-persistence.md.

Revision ID: 0001_app_users_sessions
Revises:
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_app_users_sessions"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="app",
    )

    op.create_table(
        "conversations",
        # id == AG-UI thread_id — client-supplied, no server default.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("archived_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.users.id"], ondelete="CASCADE"
        ),
        schema="app",
    )
    op.create_index(
        "ix_conversations_user_id", "conversations", ["user_id"], schema="app"
    )

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["app.conversations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "conversation_id", "seq", name="uq_messages_conversation_seq"
        ),
        schema="app",
    )
    op.create_index(
        "ix_messages_conversation_seq",
        "messages",
        ["conversation_id", "seq"],
        schema="app",
    )

    op.create_table(
        "session_state",
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["app.conversations.id"], ondelete="CASCADE"
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("session_state", schema="app")
    op.drop_index("ix_messages_conversation_seq", "messages", schema="app")
    op.drop_table("messages", schema="app")
    op.drop_index("ix_conversations_user_id", "conversations", schema="app")
    op.drop_table("conversations", schema="app")
    op.drop_table("users", schema="app")
