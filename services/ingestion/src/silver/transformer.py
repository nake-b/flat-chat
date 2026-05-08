from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_table


def _parse_german_date(date_str: str | None) -> datetime | None:
    """Parse a German date string like '01.04.2026' into a datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _safe_int(value) -> int | None:
    """Convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def transform(session: Session) -> int:
    """Read bronze rows and upsert cleaned data into the listings (silver) table.

    Returns the number of rows upserted.
    """
    raw_listings = get_table("raw_listings")
    listings = get_table("listings")

    rows = session.execute(select(raw_listings)).fetchall()

    count = 0
    for row in rows:
        data = row.data
        raw_id = row.id

        values = {
            "raw_listing_id": raw_id,
            "source_name": row.source_name,
            "external_id": row.external_id,
            "external_object_id": data.get("objectId"),
            "listing_url": data.get("url"),
            # Core details
            "title": data.get("title"),
            "headline": data.get("headline"),
            "rooms": _safe_float(data.get("rooms")),
            "area_sqm": _safe_float(data.get("areaSqm")),
            # Rent
            "cold_rent_eur": _safe_float(data.get("coldRentEur")),
            "warm_rent_eur": _safe_float(data.get("warmRentEur")),
            "nebenkosten_eur": _safe_float(data.get("nebenkostenEur")),
            "rent_gross_eur": _safe_float(data.get("rentGrossEur")),
            # Location
            "address": data.get("location"),
            "latitude": None,
            "longitude": None,
            # Building
            "floor": _safe_int(data.get("floor")),
            "floors_total": _safe_int(data.get("floorsTotal")),
            "construction_year": _safe_int(data.get("constructionYear")),
            "available_from": _parse_german_date(data.get("occupationDate")),
            # Energy
            "heating": data.get("heating"),
            "main_energy_source": data.get("mainEnergySource"),
            "energy_consumption_kwh": _safe_float(data.get("energyConsumptionKwh")),
            "final_energy_value_kwh": _safe_float(data.get("finalEnergyValueKwh")),
            "energy_pass_type": data.get("energyPassType"),
            # Amenities
            "has_elevator": data.get("elevator"),
            "has_balcony": data.get("balcony"),
            "has_basement": data.get("basement"),
            "wbs_required": data.get("wbsRequired"),
            # Company
            "company_name": data.get("company"),
            "company_website": data.get("companyWebsite"),
            # Timestamps
            "scraped_at": row.scraped_at,
        }

        stmt = pg_insert(listings).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_listing_source_external",
            set_={
                k: stmt.excluded[k]
                for k in values
                if k not in ("source_name", "external_id")
            },
        )
        session.execute(stmt)
        count += 1

    session.commit()
    return count
