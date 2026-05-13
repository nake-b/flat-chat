import uuid

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
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IronCard(Base):
    """Iron layer: raw card-level scrape output from list/search pages."""

    __tablename__ = "iron_cards"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_iron_source_external"),
        Index("ix_iron_cards_source_name", "source_name"),
        Index("ix_iron_cards_pending", "source_name", "detail_scraped_at"),
        Index("ix_iron_cards_scraped_at", "scraped_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    detail_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scraped_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ingested_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    detail_scraped_at: Mapped[str | None] = mapped_column(TIMESTAMP(timezone=True))

    raw_listings: Mapped[list["RawListing"]] = relationship(back_populates="iron_card")


class RawListing(Base):
    """Bronze layer: raw detail-page scrape preserved as JSONB."""

    __tablename__ = "raw_listings"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_raw_source_external"),
        Index("ix_raw_listings_source_name", "source_name"),
        Index("ix_raw_listings_scraped_at", "scraped_at"),
        Index("ix_raw_listings_iron_card_id", "iron_card_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    iron_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("iron_cards.id", ondelete="SET NULL"),
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scraped_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ingested_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    iron_card: Mapped["IronCard | None"] = relationship(back_populates="raw_listings")
    listing: Mapped["Listing | None"] = relationship(back_populates="raw_listing")


class Listing(Base):
    """Silver layer: cleaned, typed, normalized listing rows.

    The union of every field any source could produce. Adding a new source
    requires a new per-source transformer, not a schema migration.
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_listing_source_external"),
        Index("ix_listings_source_name", "source_name"),
        Index("ix_listings_rooms", "rooms"),
        Index("ix_listings_cold_rent_eur", "cold_rent_eur"),
        Index("ix_listings_area_sqm", "area_sqm"),
        Index("ix_listings_available_from", "available_from"),
        Index("ix_listings_wbs_required", "wbs_required"),
        Index("ix_listings_lat_lon", "latitude", "longitude"),
        Index("ix_listings_postal_code", "postal_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_listings.id", ondelete="SET NULL"),
    )

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

    # Embedding — populated by a later migration (dim TBD)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())

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

    raw_listing: Mapped["RawListing | None"] = relationship(back_populates="listing")
