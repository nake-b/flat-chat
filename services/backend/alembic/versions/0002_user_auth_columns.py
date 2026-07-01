"""app schema: user auth columns (fastapi-users)

Adds the fastapi-users contract columns to `app.users` so real password auth can
land: `email` / `hashed_password` (**NOT NULL** — every user is a real account)
+ the `is_active` / `is_superuser` / `is_verified` flags (NOT NULL with server
defaults).

`email` / `hashed_password` are NOT NULL with no default, so this migration must
run against an EMPTY `app.users` table — a fresh or refreshed dev DB. This is a
deliberate dev-only stance: there are no dummy/legacy rows to preserve (only a
seeded dev + reviewer account). On a DB that still holds pre-auth rows, clear
`app.users` (or refresh the DB) before upgrading.

Pure schema — clean `head → down → head` round-trip (every op reverses). No data
seed; the dev user is created by `scripts/seed_users.py`. See AUTH.md and
agent-compound-docs/decisions/session-persistence.md.

Revision ID: 0002_user_auth_columns
Revises: 0001_app_users_sessions
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_auth_columns"
# Chains directly off 0001, as originally authored. Bookmarks
# (0002_app_bookmarks) chains AFTER this migration, NOT before — so any DB that
# already applied auth (the state of every checkout of `main`) picks up
# bookmarks on the next `upgrade head`. Ordering the two is immaterial (auth
# adds columns to app.users; bookmarks adds a table), but bookmarks-after-auth
# is the only order that doesn't strand an already-migrated DB at the head.
down_revision: str | Sequence[str] | None = "0001_app_users_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        schema="app",
    )
    op.add_column(
        "users",
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        schema="app",
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        schema="app",
    )
    op.add_column(
        "users",
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="app",
    )
    op.add_column(
        "users",
        sa.Column(
            "is_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_users_email"), "users", ["email"], unique=True, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_users_email"), table_name="users", schema="app")
    op.drop_column("users", "is_verified", schema="app")
    op.drop_column("users", "is_superuser", schema="app")
    op.drop_column("users", "is_active", schema="app")
    op.drop_column("users", "hashed_password", schema="app")
    op.drop_column("users", "email", schema="app")
