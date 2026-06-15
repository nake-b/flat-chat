"""gold (listings_geo_context) + platinum (listings_embeddings) layers

Revision ID: 0005_gold_platinum
Revises: 0004_geo_context_hardening
Create Date: 2026-06-15

Introduces the two new medallion layers that sit above silver:

  - GOLD (`listings_geo_context`) — one row per listing, denormalised join
    of every nearby geo-context fact (transit / parks / schools / hospitals /
    noise / density / MSS / water / playground / disabled-parking). Filled
    by `services/ingestion/src/gold/`. The search hot-path now hits this
    table via B-tree filters instead of running spatial subqueries per
    request. JSONB blobs carry the detail-panel data so `open_listing`
    becomes one PK lookup instead of 12 sequential spatial queries.

  - PLATINUM (`listings_embeddings`) — semantic-search vectors as their own
    table, split out from `listings.embedding`. Lets us swap embedding
    models without schema churn on the listings table and keeps the HNSW
    index isolated to its actual consumer. Filled by
    `services/ingestion/src/platinum/`.

Existing `listings.embedding` data is copied into the new platinum table
before the column is dropped — round-trip safe.

Index policy:
  - Gold scalar chips get plain b-tree indexes — these are the columns the
    rewritten search query filters on (e.g. `WHERE nearest_transit_m <= 400
    AND mss_status = 'disadvantaged'`). Single-column indexes; Postgres
    bitmap-ANDs across them when filters combine.
  - Gold JSONB detail blobs get NO indexes — only fetched by PK
    (`listings_geo_context.listing_id = ?`), so the PK already covers them.
  - Gold `nearest_transit_lines TEXT[]` gets a GIN index for `&&` (overlap)
    filters like "near U8 or S5".
  - Platinum keeps the HNSW vector index, migrated unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_gold_platinum"
down_revision: str | Sequence[str] | None = "0004_geo_context_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -------------------------------------------------------------------------
# Forward migration
# -------------------------------------------------------------------------


def upgrade() -> None:
    # =====================================================================
    # listings_geo_context  ← gold layer
    # One row per listing. Pre-joined geo-context for every chip + detail
    # surface the agent and frontend display. Refreshed by `gold.run` after
    # silver listings ingestion and after geo-context refresh.
    # =====================================================================

    op.execute(
        """
        CREATE TABLE listings_geo_context (
            listing_id              UUID PRIMARY KEY
                                       REFERENCES listings(id) ON DELETE CASCADE,

            -- Card-level chip scalars (B-tree-filterable). Raw numbers; the
            -- chat layer applies bucket labels at result-mapping time.
            nearest_transit_lines   TEXT[],
            nearest_transit_m       INTEGER,
            nearest_transit_name    TEXT,
            nearest_park_name       TEXT,
            nearest_park_m          INTEGER,
            noise_total_lden        REAL,
            persons_per_hectare     REAL,
            mss_status              TEXT,
            mss_dynamics            TEXT,

            -- Detail-panel blobs. Frozen at gold-build time; fetched as one
            -- PK lookup by `ListingService.get(id)` / `GET /api/listings/{id}`.
            transit_top3            JSONB,  -- list[NearestTransitStop]
            school_catchment        JSONB,  -- SchoolCatchmentInfo | null
            schools_top3            JSONB,  -- list[NearestSchool]
            parks_top2              JSONB,  -- list[NearestPark]
            playground              JSONB,  -- NearestPlayground | null
            hospitals_top2          JSONB,  -- list[NearestHospital]
            water                   JSONB,  -- NearestWater | null
            noise_profile           JSONB,  -- NoiseProfile { street_lden, rail_lden, total_lden }
            greenery_profile        JSONB,  -- GreeneryProfile { green_m2_within_300m }
            density_profile         JSONB,  -- DensityProfile { persons_per_ha, age_buckets }
            mss_profile             JSONB,  -- MssProfile { status, dynamics, social_inequality, residents }
            disabled_parking_count  INTEGER,

            enriched_at             TIMESTAMP WITH TIME ZONE
                                       NOT NULL DEFAULT now()
        )
        """
    )

    # B-tree indexes — one per filterable scalar. Single-column; Postgres
    # bitmap-ANDs across them when search combines filters.
    op.execute(
        "CREATE INDEX ix_lgc_nearest_transit_m "
        "ON listings_geo_context (nearest_transit_m) "
        "WHERE nearest_transit_m IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_nearest_park_m "
        "ON listings_geo_context (nearest_park_m) "
        "WHERE nearest_park_m IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_noise_total_lden "
        "ON listings_geo_context (noise_total_lden) "
        "WHERE noise_total_lden IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_persons_per_hectare "
        "ON listings_geo_context (persons_per_hectare) "
        "WHERE persons_per_hectare IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_mss_status "
        "ON listings_geo_context (mss_status) "
        "WHERE mss_status IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_lgc_mss_dynamics "
        "ON listings_geo_context (mss_dynamics) "
        "WHERE mss_dynamics IS NOT NULL"
    )

    # GIN index on the transit-lines TEXT[] so "near U8 or S5" filters can
    # use the array-overlap operator `&&` index-aware.
    op.execute(
        "CREATE INDEX ix_lgc_nearest_transit_lines "
        "ON listings_geo_context USING GIN (nearest_transit_lines)"
    )

    # =====================================================================
    # listings_embeddings  ← platinum layer (vector store)
    # Embeddings extracted from `listings.embedding`. Separate table so we
    # can: a) swap embedding models without schema churn on listings,
    #      b) keep the HNSW index isolated to its consumer,
    #      c) treat embeddings as a per-listing transformation owned by its
    #         own ingestion module (`services/ingestion/src/platinum/`).
    # =====================================================================

    op.execute(
        """
        CREATE TABLE listings_embeddings (
            listing_id    UUID PRIMARY KEY
                              REFERENCES listings(id) ON DELETE CASCADE,
            embedding     vector(1024) NOT NULL,
            model_name    TEXT NOT NULL,
            embedded_at   TIMESTAMP WITH TIME ZONE
                              NOT NULL DEFAULT now()
        )
        """
    )

    # HNSW ANN index for cosine-distance ORDER BY. Same parameters as the
    # old `listings_embedding_hnsw_idx` from 0002.
    op.execute(
        """
        CREATE INDEX listings_embeddings_hnsw_idx
        ON listings_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    # Copy existing embeddings into the platinum table so semantic search
    # keeps working without a fresh re-embed run. Model name 'jina-v3-1024'
    # matches the historical default locked in revision 0002 (Jina v3,
    # 1024 dims). Future re-embeds via `platinum.run` write their own
    # model_name.
    op.execute(
        """
        INSERT INTO listings_embeddings (listing_id, embedding, model_name)
        SELECT id, embedding, 'jina-v3-1024'
        FROM listings
        WHERE embedding IS NOT NULL
        """
    )

    # Now the old column is redundant. Drop it + its HNSW index.
    op.execute("DROP INDEX IF EXISTS listings_embedding_hnsw_idx")
    op.execute("ALTER TABLE listings DROP COLUMN IF EXISTS embedding")


# -------------------------------------------------------------------------
# Rollback migration
# -------------------------------------------------------------------------


def downgrade() -> None:
    # Restore the embedding column on listings. Existing platinum data is
    # copied back; bad data (multiple model_names per listing) is impossible
    # due to the PK on listings_embeddings.listing_id.
    op.execute("ALTER TABLE listings ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute(
        """
        UPDATE listings
        SET embedding = le.embedding
        FROM listings_embeddings le
        WHERE listings.id = le.listing_id
        """
    )
    op.execute(
        """
        CREATE INDEX listings_embedding_hnsw_idx
        ON listings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    # Drop platinum.
    op.execute("DROP TABLE IF EXISTS listings_embeddings")

    # Drop gold indexes + table.
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_transit_lines")
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_dynamics")
    op.execute("DROP INDEX IF EXISTS ix_lgc_mss_status")
    op.execute("DROP INDEX IF EXISTS ix_lgc_persons_per_hectare")
    op.execute("DROP INDEX IF EXISTS ix_lgc_noise_total_lden")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_park_m")
    op.execute("DROP INDEX IF EXISTS ix_lgc_nearest_transit_m")
    op.execute("DROP TABLE IF EXISTS listings_geo_context")
