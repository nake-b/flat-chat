"""app schema: bookmarks

Per-user saved listings. A many-to-many between `app.users` and
`world.listings`. The cross-schema FK works because both tables live in
one DB; the constraint is enforced even though `world.listings` migrations
are owned by the ingestion service. The composite PK `(user_id, listing_id)`
makes `ON CONFLICT DO NOTHING` upserts trivial and removes any need for a
synthetic id column.

CASCADE on both FKs: deleting a user wipes their bookmarks (already true
for conversations); deleting a listing via ingestion sweeps every bookmark
pointing at it so a stale FK target can never linger.

Index on `(user_id, created_at DESC)` lets the sidebar's newest-first
ORDER BY be index-only.

Revision ID: 0002_app_bookmarks
Revises: 0002_user_auth_columns
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_app_bookmarks"
# Chained AFTER 0002_user_auth_columns (not before) so a DB that already
# applied auth — every checkout of `main` — picks bookmarks up on the next
# `upgrade head`. Chaining before auth would leave such a DB already at the head
# and never create app.bookmarks. Single linear head: 0001 → auth → bookmarks.
down_revision: str | Sequence[str] | None = "0002_user_auth_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id", "listing_id", name="pk_bookmarks"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.users.id"],
            ondelete="CASCADE",
            name="fk_bookmarks_user",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["world.listings.id"],
            ondelete="CASCADE",
            name="fk_bookmarks_listing",
        ),
        schema="app",
    )
    op.create_index(
        "ix_bookmarks_user_created",
        "bookmarks",
        ["user_id", sa.text("created_at DESC")],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index("ix_bookmarks_user_created", "bookmarks", schema="app")
    op.drop_table("bookmarks", schema="app")
