"""spatial junction tables — one per POI family + drop redundant JSONB blobs

Revision ID: 0006_spatial_junction_tables
Revises: 0005_gold_platinum
Create Date: 2026-06-15

Closes the precision gaps the v1 gold shape introduced:

  - transit.lines / transit.stop_name silently narrowed to the nearest stop
    only (a U8 stop 400 m away no longer matches "near U8" when the nearest
    is U1).
  - school + hospital filters silently dropped distance + type/tier args
    because the gold representation was a non-null JSONB blob, not a
    queryable set of rows.

Solution: per-POI-family junction tables, one row per `(listing × feature)`
pair within a generous radius. Filter EXISTS against the junction table
honours any attribute filter (modes, lines, school_type, hospital tier,
...). Single-attribute "within X meters" filters (parks, playgrounds,
water) also move here for shape symmetry — no current attribute filter,
but the junction table is there when one's added.

Population rule per listing (handled by `services/ingestion/src/gold/
enrich_listings.py`): top-K=5 always-include ∪ all features within R.
K=5 guarantees the detail panel renders even in feature-sparse periphery;
within-R covers the filter use-case. See
`agent-compound-docs/decisions/spatial-neighbor-tables.md`.

Storage radii (per-family, generous side intentionally — search-time
predicates do the actual cutoff):

  | family       | R (km) |
  | transit      |   5    |
  | schools      |   5    |
  | hospitals    |  12    |
  | parks        |   5    |
  | playgrounds  |   3    |
  | water        |   6    |

Drops 6 redundant JSONB columns from `listings_geo_context`:
`transit_top3`, `schools_top3`, `parks_top2`, `playground`,
`hospitals_top2`, `water`. The junction tables are now the canonical
source for detail-panel rendering (one PK lookup → top-N by rank).

Kept on `listings_geo_context`:
  - chip scalars (`nearest_transit_m`, `nearest_transit_lines`,
    `nearest_transit_name`, `nearest_park_m`, `nearest_park_name`) —
    derived from the junction tables at gold-build time; used by the
    card-row projection for label rendering ("10 min to park").
  - scalar / field columns (`noise_total_lden`, `persons_per_hectare`,
    `mss_status`, `mss_dynamics`, `noise_profile`, `greenery_profile`,
    `density_profile`, `mss_profile`, `school_catchment`,
    `disabled_parking_count`) — these are scalar facts about the
    listing's location, not POI sets.

Index policy:
  - PK on (listing_id, feature_id) — covers PK lookups for ListingService.
  - B-tree on (listing_id, distance_m) — covers EXISTS filters with the
    `nbr.listing_id = l.id AND nbr.distance_m <= X` shape.
  - GIN on TEXT[]/INT[] attribute columns where applicable (transit
    modes/lines) so `&&` overlap filters hit an index.
  - B-tree on scalar TEXT attributes where applicable (school_type,
    hospital tier) for direct equality / ILIKE selectivity.

Round-trip: downgrade re-adds the dropped JSONB columns as NULL. They're
gold-derivable, so a subsequent `gold.run` refill is the recovery path.
**Caveat:** the v1 gold code that populated those JSONB blobs is gone in
this revision range. Downgrading past 0006 in production therefore needs
the pre-0006 backend code checked out first, then `gold.run`, before the
columns will hold real values again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_spatial_junction_tables"
down_revision: str | Sequence[str] | None = "0005_gold_platinum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -------------------------------------------------------------------------
# Forward migration
# -------------------------------------------------------------------------


def upgrade() -> None:
    # =====================================================================
    # listings_nearby_transit  ← transit-stop junction
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_transit (
            listing_id  UUID NOT NULL
                            REFERENCES listings(id) ON DELETE CASCADE,
            stop_id     TEXT NOT NULL,
            distance_m  INTEGER NOT NULL,
            modes       INTEGER[] NOT NULL,
            lines       TEXT[] NOT NULL,
            name        TEXT,
            rank        SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, stop_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnt_listing_distance "
        "ON listings_nearby_transit (listing_id, distance_m)"
    )
    op.execute(
        "CREATE INDEX ix_lnt_modes "
        "ON listings_nearby_transit USING GIN (modes)"
    )
    op.execute(
        "CREATE INDEX ix_lnt_lines "
        "ON listings_nearby_transit USING GIN (lines)"
    )

    # =====================================================================
    # listings_nearby_schools  ← school junction
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_schools (
            listing_id   UUID NOT NULL
                             REFERENCES listings(id) ON DELETE CASCADE,
            school_id    TEXT NOT NULL,
            distance_m   INTEGER NOT NULL,
            school_type  TEXT,
            name         TEXT,
            rank         SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, school_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lns_listing_distance "
        "ON listings_nearby_schools (listing_id, distance_m)"
    )
    op.execute(
        "CREATE INDEX ix_lns_school_type "
        "ON listings_nearby_schools (school_type) "
        "WHERE school_type IS NOT NULL"
    )

    # =====================================================================
    # listings_nearby_hospitals  ← hospital junction
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_hospitals (
            listing_id   UUID NOT NULL
                             REFERENCES listings(id) ON DELETE CASCADE,
            hospital_id  TEXT NOT NULL,
            distance_m   INTEGER NOT NULL,
            tier         TEXT,
            name         TEXT,
            rank         SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, hospital_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnh_listing_distance "
        "ON listings_nearby_hospitals (listing_id, distance_m)"
    )
    op.execute(
        "CREATE INDEX ix_lnh_tier "
        "ON listings_nearby_hospitals (tier) "
        "WHERE tier IS NOT NULL"
    )

    # =====================================================================
    # listings_nearby_parks  ← park junction (cemeteries excluded at ETL)
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_parks (
            listing_id   UUID NOT NULL
                             REFERENCES listings(id) ON DELETE CASCADE,
            park_id      TEXT NOT NULL,
            distance_m   INTEGER NOT NULL,
            object_type  TEXT,
            name         TEXT,
            rank         SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, park_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnp_listing_distance "
        "ON listings_nearby_parks (listing_id, distance_m)"
    )

    # =====================================================================
    # listings_nearby_playgrounds  ← playground junction
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_playgrounds (
            listing_id      UUID NOT NULL
                                REFERENCES listings(id) ON DELETE CASCADE,
            playground_id   TEXT NOT NULL,
            distance_m      INTEGER NOT NULL,
            name            TEXT,
            rank            SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, playground_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnpg_listing_distance "
        "ON listings_nearby_playgrounds (listing_id, distance_m)"
    )

    # =====================================================================
    # listings_nearby_water  ← water-body junction
    # =====================================================================
    op.execute(
        """
        CREATE TABLE listings_nearby_water (
            listing_id   UUID NOT NULL
                             REFERENCES listings(id) ON DELETE CASCADE,
            water_id     TEXT NOT NULL,
            distance_m   INTEGER NOT NULL,
            water_kind   TEXT,
            name         TEXT,
            rank         SMALLINT NOT NULL,
            PRIMARY KEY (listing_id, water_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lnw_listing_distance "
        "ON listings_nearby_water (listing_id, distance_m)"
    )

    # =====================================================================
    # Drop redundant JSONB blobs from listings_geo_context — the junction
    # tables now own the per-listing detail surface.
    # =====================================================================
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS transit_top3")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS schools_top3")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS parks_top2")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS playground")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS hospitals_top2")
    op.execute("ALTER TABLE listings_geo_context DROP COLUMN IF EXISTS water")


# -------------------------------------------------------------------------
# Rollback migration
# -------------------------------------------------------------------------


def downgrade() -> None:
    # Restore the JSONB columns on listings_geo_context. They come back as
    # NULL; the recovery path is `docker compose --profile gold run --rm
    # gold` to refill them under the v1 (pre-junction) shape — that gold
    # codepath is also gone after this commit, so practically downgrade
    # past 0006 implies checking out a pre-0006 backend revision first.
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS transit_top3 JSONB")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS schools_top3 JSONB")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS parks_top2 JSONB")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS playground JSONB")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS hospitals_top2 JSONB")
    op.execute("ALTER TABLE listings_geo_context ADD COLUMN IF NOT EXISTS water JSONB")

    # Drop junction tables (CASCADE on FK takes care of indexes).
    op.execute("DROP TABLE IF EXISTS listings_nearby_water")
    op.execute("DROP TABLE IF EXISTS listings_nearby_playgrounds")
    op.execute("DROP TABLE IF EXISTS listings_nearby_parks")
    op.execute("DROP TABLE IF EXISTS listings_nearby_hospitals")
    op.execute("DROP TABLE IF EXISTS listings_nearby_schools")
    op.execute("DROP TABLE IF EXISTS listings_nearby_transit")
