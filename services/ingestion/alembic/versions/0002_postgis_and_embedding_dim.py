"""postgis + 1024-dim embedding + location column + ANN/geo indexes

Revision ID: 0002_postgis_and_embedding_dim
Revises: 0001_initial_schema
Create Date: 2026-05-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002_postgis_and_embedding_dim"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # postgis extension is created by the postgres bootstrap, not here.
    # Lock the embedding column to Jina v3's 1024 dims. Safe to ALTER while
    # there's no data; if rows already carry NULL embeddings, pgvector accepts
    # the type change.
    op.alter_column(
        "listings",
        "embedding",
        type_=Vector(1024),
        existing_nullable=True,
        postgresql_using="embedding::vector(1024)",
    )

    # PostGIS Point column. Populated from latitude/longitude here for any
    # pre-existing rows; the silver transformer keeps it in sync going forward.
    op.execute(
        "ALTER TABLE listings ADD COLUMN location geometry(Point, 4326)"
    )
    op.execute(
        """
        UPDATE listings
        SET location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """
    )

    # HNSW for cosine-distance ORDER BY on the embedding.
    op.execute(
        """
        CREATE INDEX listings_embedding_hnsw_idx
        ON listings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    # Functional GiST on (location::geography) so ST_DWithin queries casting
    # to geography (radius in meters on the WGS84 spheroid) hit an index.
    op.execute(
        """
        CREATE INDEX listings_location_geog_idx
        ON listings
        USING gist ((location::geography))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS listings_location_geog_idx")
    op.execute("DROP INDEX IF EXISTS listings_embedding_hnsw_idx")
    op.execute("ALTER TABLE listings DROP COLUMN IF EXISTS location")
    op.alter_column(
        "listings",
        "embedding",
        type_=Vector(),
        existing_nullable=True,
        postgresql_using="embedding::vector",
    )
    # PostGIS extension intentionally left installed — other migrations or
    # external objects may depend on it.
