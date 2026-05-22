"""initial schema: iron_cards, raw_listings, listings + pgvector

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "iron_cards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("detail_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "scraped_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("detail_scraped_at", postgresql.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint(
            "source_name", "external_id", name="uq_iron_source_external"
        ),
    )
    op.create_index(
        "ix_iron_cards_source_name", "iron_cards", ["source_name"]
    )
    op.create_index(
        "ix_iron_cards_pending",
        "iron_cards",
        ["source_name", "detail_scraped_at"],
    )
    op.create_index(
        "ix_iron_cards_scraped_at", "iron_cards", ["scraped_at"]
    )

    op.create_table(
        "raw_listings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "iron_card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("iron_cards.id", ondelete="SET NULL"),
        ),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "scraped_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "source_name", "external_id", name="uq_raw_source_external"
        ),
    )
    op.create_index(
        "ix_raw_listings_source_name", "raw_listings", ["source_name"]
    )
    op.create_index(
        "ix_raw_listings_scraped_at", "raw_listings", ["scraped_at"]
    )
    op.create_index(
        "ix_raw_listings_iron_card_id", "raw_listings", ["iron_card_id"]
    )

    op.create_table(
        "listings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "raw_listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_listings.id", ondelete="SET NULL"),
        ),
        # Source
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("external_object_id", sa.String(255)),
        sa.Column("listing_url", sa.Text()),
        # Core
        sa.Column("title", sa.Text()),
        sa.Column("headline", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("rooms", sa.Float()),
        sa.Column("bedrooms", sa.Integer()),
        sa.Column("bathrooms", sa.Integer()),
        sa.Column("area_sqm", sa.Float()),
        sa.Column("apartment_type", sa.String(100)),
        # Rent
        sa.Column("cold_rent_eur", sa.Float()),
        sa.Column("warm_rent_eur", sa.Float()),
        sa.Column("nebenkosten_eur", sa.Float()),
        sa.Column("rent_gross_eur", sa.Float()),
        sa.Column("kaution_eur", sa.Float()),
        # Location
        sa.Column("address", sa.Text()),
        sa.Column("postal_code", sa.String(10)),
        sa.Column("district", sa.String(100)),
        sa.Column("city", sa.String(100)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        # Building / availability
        sa.Column("floor", sa.Integer()),
        sa.Column("floors_total", sa.Integer()),
        sa.Column("construction_year", sa.Integer()),
        sa.Column("available_from", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("available_until", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("min_stay_months", sa.Integer()),
        sa.Column("max_stay_months", sa.Integer()),
        # Energy
        sa.Column("heating", sa.String(255)),
        sa.Column("main_energy_source", sa.String(255)),
        sa.Column("energy_consumption_kwh", sa.Float()),
        sa.Column("final_energy_value_kwh", sa.Float()),
        sa.Column("energy_pass_type", sa.String(100)),
        # Amenities
        sa.Column("is_furnished", sa.Boolean()),
        sa.Column("has_kitchen", sa.Boolean()),
        sa.Column("has_bathroom", sa.Boolean()),
        sa.Column("has_elevator", sa.Boolean()),
        sa.Column("has_balcony", sa.Boolean()),
        sa.Column("has_terrace", sa.Boolean()),
        sa.Column("has_garden", sa.Boolean()),
        sa.Column("has_basement", sa.Boolean()),
        sa.Column(
            "wbs_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Lister
        sa.Column("lister_type", sa.String(50)),
        sa.Column("company_name", sa.String(255)),
        sa.Column("company_website", sa.Text()),
        # Free-form
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("key_facts", postgresql.JSONB(astext_type=sa.Text())),
        # Embedding
        sa.Column("embedding", Vector()),
        # Timestamps
        sa.Column(
            "scraped_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
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
        sa.UniqueConstraint(
            "source_name", "external_id", name="uq_listing_source_external"
        ),
    )
    op.create_index("ix_listings_source_name", "listings", ["source_name"])
    op.create_index("ix_listings_rooms", "listings", ["rooms"])
    op.create_index("ix_listings_cold_rent_eur", "listings", ["cold_rent_eur"])
    op.create_index("ix_listings_area_sqm", "listings", ["area_sqm"])
    op.create_index("ix_listings_available_from", "listings", ["available_from"])
    op.create_index("ix_listings_wbs_required", "listings", ["wbs_required"])
    op.create_index("ix_listings_lat_lon", "listings", ["latitude", "longitude"])
    op.create_index("ix_listings_postal_code", "listings", ["postal_code"])

    # Keep updated_at fresh on UPDATE (SQLAlchemy's onupdate fires only via the ORM;
    # this trigger ensures consistency for raw SQL / pg_insert paths used by ingestion).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION listings_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_listings_set_updated_at
        BEFORE UPDATE ON listings
        FOR EACH ROW
        EXECUTE FUNCTION listings_set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_listings_set_updated_at ON listings")
    op.execute("DROP FUNCTION IF EXISTS listings_set_updated_at()")
    op.drop_table("listings")
    op.drop_table("raw_listings")
    op.drop_table("iron_cards")
    op.execute("DROP EXTENSION IF EXISTS vector")
