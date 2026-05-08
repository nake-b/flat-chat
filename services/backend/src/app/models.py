import uuid

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


class RawListing(Base):
    """Bronze layer: raw scraped data preserved as JSONB."""

    __tablename__ = "raw_listings"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_raw_source_external"),
        Index("ix_raw_listings_source_name", "source_name"),
        Index("ix_raw_listings_scraped_at", "scraped_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scraped_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ingested_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    listing: Mapped["Listing | None"] = relationship(back_populates="raw_listing")


class Listing(Base):
    """Silver layer: cleaned, typed columns extracted from bronze."""

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
    rooms: Mapped[float | None] = mapped_column(Float)
    area_sqm: Mapped[float | None] = mapped_column(Float)

    # Rent
    cold_rent_eur: Mapped[float | None] = mapped_column(Float)
    warm_rent_eur: Mapped[float | None] = mapped_column(Float)
    nebenkosten_eur: Mapped[float | None] = mapped_column(Float)
    rent_gross_eur: Mapped[float | None] = mapped_column(Float)

    # Location
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Building
    floor: Mapped[int | None] = mapped_column(Integer)
    floors_total: Mapped[int | None] = mapped_column(Integer)
    construction_year: Mapped[int | None] = mapped_column(Integer)
    available_from: Mapped[str | None] = mapped_column(TIMESTAMP(timezone=True))

    # Energy
    heating: Mapped[str | None] = mapped_column(String(255))
    main_energy_source: Mapped[str | None] = mapped_column(String(255))
    energy_consumption_kwh: Mapped[float | None] = mapped_column(Float)
    final_energy_value_kwh: Mapped[float | None] = mapped_column(Float)
    energy_pass_type: Mapped[str | None] = mapped_column(String(100))

    # Amenities
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_basement: Mapped[bool | None] = mapped_column(Boolean)
    wbs_required: Mapped[bool | None] = mapped_column(Boolean)

    # Company
    company_name: Mapped[str | None] = mapped_column(String(255))
    company_website: Mapped[str | None] = mapped_column(Text)

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
