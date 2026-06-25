"""remove MSS layer leftovers

Revision ID: 006
Revises: 0005_gold_platinum
Create Date: 2026-06-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str | Sequence[str] | None = "0005_gold_platinum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS social_monitoring_2025")
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_status")
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_dynamics")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_status")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_dynamics")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS mss_profile")


def downgrade() -> None:
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_status TEXT")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_dynamics TEXT")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS mss_profile JSONB")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_mss_status "
        "ON listings_geo_context (mss_status) WHERE mss_status IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lgc_mss_dynamics "
        "ON listings_geo_context (mss_dynamics) WHERE mss_dynamics IS NOT NULL"
    )
