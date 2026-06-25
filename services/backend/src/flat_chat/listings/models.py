"""ORM models for the listings domain — READ-ONLY views of the `world` schema.

Every table here lives in the `world` Postgres schema, owned and migrated by
the INGESTION service (services/ingestion/alembic/). The backend never writes
or migrates them — it reads them. These classes are the read side of the
shared-kernel contract; a drift test (tests) checks them against the live
`world` schema. See schema-ownership-split.md.

  - Silver: `Listing` — cleaned, typed, normalised. Source-faithful per
    entity. This is the canonical "an apartment exists" record. (Its
    iron/bronze provenance — `iron_cards` / `raw_listings` — is
    ingestion-internal; the backend doesn't model those tables. `Listing`
    keeps `raw_listing_id` as a plain column for fidelity, without an ORM FK.)
  - Gold: `ListingGeoContext` — denormalised pre-joined geo-context. One
    row per listing. Populated by `services/ingestion/src/gold/`. Search
    queries `listings ⨝ listings_geo_context` for chip-level filtering.
  - Platinum: `ListingEmbedding` — semantic-search vectors. Split out so
    the HNSW index lives only on the table that uses it.

Moved from `search/models.py` (the old home). Search no longer owns
domain types — it's filter + rank only. Same `Base` declarative root as
before.
"""

from __future__ import annotations

import uuid

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flat_chat.core.database import Base


class Listing(Base):
    """Silver layer: cleaned, typed, normalized listing rows.

    The union of every field any source could produce. Adding a new source
    requires a new per-source transformer, not a schema migration.

    Embeddings live in the platinum layer (`ListingEmbedding`) — split out
    in migration 0005 so swapping models is a platinum-only refresh.
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "external_id", name="uq_listing_source_external"
        ),
        Index("ix_listings_source_name", "source_name"),
        Index("ix_listings_rooms", "rooms"),
        Index("ix_listings_cold_rent_eur", "cold_rent_eur"),
        Index("ix_listings_area_sqm", "area_sqm"),
        Index("ix_listings_available_from", "available_from"),
        Index("ix_listings_wbs_required", "wbs_required"),
        Index("ix_listings_lat_lon", "latitude", "longitude"),
        Index("ix_listings_postal_code", "postal_code"),
        # Functional GiST so ST_DWithin queries that cast to ::geography
        # (radius in meters) can hit an index. GeoAlchemy2 already auto-creates
        # a plain GiST on the geometry column itself.
        Index(
            "listings_location_geog_idx",
            text("(location::geography)"),
            postgresql_using="gist",
        ),
        {"schema": "world"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Provenance pointer to world.raw_listings. Plain column (no ORM FK) — the
    # actual FK constraint is ingestion-owned in the world schema; the backend
    # neither models raw_listings nor joins to it.
    raw_listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Source
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_object_id: Mapped[str | None] = mapped_column(String(255))
    listing_url: Mapped[str | None] = mapped_column(Text)

    # Core details
    title: Mapped[str | None] = mapped_column(Text)
    headline: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    area_sqm: Mapped[float | None] = mapped_column(Float)
    apartment_type: Mapped[str | None] = mapped_column(String(100))

    # Rent
    cold_rent_eur: Mapped[float | None] = mapped_column(Float)
    warm_rent_eur: Mapped[float | None] = mapped_column(Float)
    nebenkosten_eur: Mapped[float | None] = mapped_column(Float)
    rent_gross_eur: Mapped[float | None] = mapped_column(Float)
    kaution_eur: Mapped[float | None] = mapped_column(Float)

    # Location
    address: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(String(10))
    district: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # PostGIS Point in WGS84 — kept in sync with latitude/longitude at
    # silver-transform time (and via the 0002 backfill for existing rows).
    location = mapped_column(Geometry("POINT", srid=4326), nullable=True)

    # Building / availability
    floor: Mapped[int | None] = mapped_column(Integer)
    floors_total: Mapped[int | None] = mapped_column(Integer)
    construction_year: Mapped[int | None] = mapped_column(Integer)
    available_from: Mapped[str | None] = mapped_column(TIMESTAMP(timezone=True))
    available_until: Mapped[str | None] = mapped_column(TIMESTAMP(timezone=True))
    min_stay_months: Mapped[int | None] = mapped_column(Integer)
    max_stay_months: Mapped[int | None] = mapped_column(Integer)

    # Energy
    heating: Mapped[str | None] = mapped_column(String(255))
    main_energy_source: Mapped[str | None] = mapped_column(String(255))
    energy_consumption_kwh: Mapped[float | None] = mapped_column(Float)
    final_energy_value_kwh: Mapped[float | None] = mapped_column(Float)
    energy_pass_type: Mapped[str | None] = mapped_column(String(100))

    # Amenities (booleans)
    is_furnished: Mapped[bool | None] = mapped_column(Boolean)
    has_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_bathroom: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_basement: Mapped[bool | None] = mapped_column(Boolean)
    wbs_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Listing source metadata (NO personal data — only type)
    lister_type: Mapped[str | None] = mapped_column(String(50))
    company_name: Mapped[str | None] = mapped_column(String(255))
    company_website: Mapped[str | None] = mapped_column(Text)

    # Free-form structured data
    features: Mapped[list | None] = mapped_column(JSONB)
    images: Mapped[list | None] = mapped_column(JSONB)
    key_facts: Mapped[dict | None] = mapped_column(JSONB)

    # Timestamps
    scraped_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ingested_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    geo_context: Mapped[ListingGeoContext | None] = relationship(
        back_populates="listing", uselist=False
    )
    embedding_row: Mapped[ListingEmbedding | None] = relationship(
        back_populates="listing", uselist=False
    )


class ListingGeoContext(Base):
    """Gold layer: pre-joined geo-context per listing.

    One row per listing. Filled by `services/ingestion/src/gold/`. The
    search hot-path reads these columns via B-tree filters; the detail
    panel reads the JSONB blobs via a single PK lookup.

    Scalar chip columns store RAW numbers — bucket labels are applied at
    the chat presentation layer via `listings.labels` (so threshold
    tweaks don't require a gold rebuild).
    """

    __tablename__ = "listings_geo_context"
    __table_args__ = {"schema": "world"}

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Card-level chip scalars (B-tree-filterable)
    nearest_transit_lines: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    nearest_transit_m: Mapped[int | None] = mapped_column(Integer)
    nearest_transit_name: Mapped[str | None] = mapped_column(Text)
    nearest_park_name: Mapped[str | None] = mapped_column(Text)
    nearest_park_m: Mapped[int | None] = mapped_column(Integer)
    noise_total_lden: Mapped[float | None] = mapped_column(Float)
    persons_per_hectare: Mapped[float | None] = mapped_column(Float)
    mss_status: Mapped[str | None] = mapped_column(Text)
    mss_dynamics: Mapped[str | None] = mapped_column(Text)

    # Scalar / field detail blobs — properties of the listing's location,
    # not POI sets. Frozen at gold-build time; one PK lookup feeds the
    # detail panel.
    school_catchment: Mapped[dict | None] = mapped_column(JSONB)
    noise_profile: Mapped[dict | None] = mapped_column(JSONB)
    greenery_profile: Mapped[dict | None] = mapped_column(JSONB)
    density_profile: Mapped[dict | None] = mapped_column(JSONB)
    mss_profile: Mapped[dict | None] = mapped_column(JSONB)
    disabled_parking_count: Mapped[int | None] = mapped_column(Integer)

    # POI-set detail blobs (`transit_top3` / `schools_top3` / `parks_top2`
    # / `playground` / `hospitals_top2` / `water`) used to live here.
    # Dropped in 0006; the junction tables (`listings_nearby_*`) are the
    # canonical source now. See
    # `agent-compound-docs/decisions/spatial-neighbor-tables.md`.

    enriched_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    listing: Mapped[Listing] = relationship(back_populates="geo_context")


class ListingEmbedding(Base):
    """Platinum layer: vector embeddings for semantic search.

    One row per listing. Filled by `services/ingestion/src/platinum/`.
    HNSW ANN index on `embedding` lives only on this table. Schema-level
    `vector(1024)` matches the Jina v3 model dim.
    """

    __tablename__ = "listings_embeddings"
    __table_args__ = (
        Index(
            "listings_embeddings_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    embedded_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    listing: Mapped[Listing] = relationship(back_populates="embedding_row")


# =========================================================================
# POI junction tables — one row per `(listing × feature)` pair within R.
# Populated by `services/ingestion/src/gold/`. The search hot-path queries
# them with EXISTS + B-tree predicates on `(listing_id, distance_m)`. The
# detail panel reads top-N by `rank` via `ListingService.get`.
# =========================================================================


class ListingNearbyTransit(Base):
    """Junction: listing × transit stop within R = 5 km."""

    __tablename__ = "listings_nearby_transit"
    __table_args__ = (
        Index("ix_lnt_listing_distance", "listing_id", "distance_m"),
        Index("ix_lnt_modes", "modes", postgresql_using="gin"),
        Index("ix_lnt_lines", "lines", postgresql_using="gin"),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stop_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    modes: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    lines: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ListingNearbySchool(Base):
    """Junction: listing × school within R = 5 km."""

    __tablename__ = "listings_nearby_schools"
    __table_args__ = (
        Index("ix_lns_listing_distance", "listing_id", "distance_m"),
        Index(
            "ix_lns_school_type",
            "school_type",
            postgresql_where=text("school_type IS NOT NULL"),
        ),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    school_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    school_type: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ListingNearbyHospital(Base):
    """Junction: listing × hospital within R = 12 km."""

    __tablename__ = "listings_nearby_hospitals"
    __table_args__ = (
        Index("ix_lnh_listing_distance", "listing_id", "distance_m"),
        Index(
            "ix_lnh_tier",
            "tier",
            postgresql_where=text("tier IS NOT NULL"),
        ),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    hospital_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ListingNearbyPark(Base):
    """Junction: listing × park within R = 5 km. Cemeteries excluded at ETL."""

    __tablename__ = "listings_nearby_parks"
    __table_args__ = (
        Index("ix_lnp_listing_distance", "listing_id", "distance_m"),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    park_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ListingNearbyPlayground(Base):
    """Junction: listing × playground within R = 3 km."""

    __tablename__ = "listings_nearby_playgrounds"
    __table_args__ = (
        Index("ix_lnpg_listing_distance", "listing_id", "distance_m"),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    playground_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ListingNearbyWater(Base):
    """Junction: listing × water body within R = 6 km."""

    __tablename__ = "listings_nearby_water"
    __table_args__ = (
        Index("ix_lnw_listing_distance", "listing_id", "distance_m"),
        {"schema": "world"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("world.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    water_id: Mapped[str] = mapped_column(Text, primary_key=True)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    water_kind: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
