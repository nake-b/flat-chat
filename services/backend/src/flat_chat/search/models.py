import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import TIMESTAMP, Float, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from flat_chat.core.database import Base


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_listing_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price_warm_eur: Mapped[float | None] = mapped_column(Numeric)
    price_cold_eur: Mapped[float | None] = mapped_column(Numeric)
    nebenkosten_eur: Mapped[float | None] = mapped_column(Numeric)
    kaution_eur: Mapped[float | None] = mapped_column(Numeric)
    area_sqm: Mapped[float | None] = mapped_column(Numeric)
    rooms: Mapped[float | None] = mapped_column(Numeric)
    floor: Mapped[int | None] = mapped_column(Integer)
    district: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float(precision=53))
    longitude: Mapped[float | None] = mapped_column(Float(precision=53))
    location = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    available_from: Mapped[str | None] = mapped_column(Text)
    available_until: Mapped[str | None] = mapped_column(Text)
    listing_type: Mapped[str | None] = mapped_column(Text)
    features: Mapped[dict | None] = mapped_column(JSONB, server_default="'[]'")
    images: Mapped[dict | None] = mapped_column(JSONB, server_default="'[]'")
    raw: Mapped[dict | None] = mapped_column(JSONB)
    description_embedding = mapped_column(Vector(1024))
    scraped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )
