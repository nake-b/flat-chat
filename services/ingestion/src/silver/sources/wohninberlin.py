"""Wohninberlin (inberlinwohnen.de) bronze→silver transformer.

Single-step source: the apartment-finder cards already carry the full
listing — price breakdown, energy data, amenity flags, and a complete
address — so there is no detail-scrape phase. The scraper writes each
card straight into `raw_listings` under `data.dump`; this transformer
maps that dump to the `listings` columns.

No geo coordinates are exposed on the cards, so `latitude`/`longitude`
stay NULL and these listings skip the gold geo-context layer. Postal
code and district are parsed out of the card's address string instead.

Money shape (verified against a real scrape):
  headline € == Kaltmiete  → cold_rent_eur  (coldRentEur)
  Gesamtmiete  (all-in)    → warm_rent_eur  (warmRentEur)
  Nebenkosten              → nebenkosten_eur
  rentGrossEur is the Bruttokaltmiete (cold + Nebenkosten); kept in
  key_facts so warm_rent_eur stays the cross-source "what you pay" figure.
"""

from __future__ import annotations

from typing import Any

from .common import (
    parse_german_date,
    parse_int_str,
    parse_postal_district,
    parse_sqm,
)


def _num(v: Any) -> float | int | None:
    """Pass real numbers through; everything else (incl. strings) -> None."""
    return v if isinstance(v, (int, float)) else None


def _positive(v: Any) -> float | int | None:
    """Numeric value, but 0 (the source's 'unknown' sentinel) -> None."""
    n = _num(v)
    return n if n else None


def _location_parts(location: str | None) -> tuple[str | None, str | None, str | None]:
    """'Glasgower Straße 17, 13349 Mitte' -> (address, postal, district)."""
    if not location:
        return None, None, None
    address = location.strip()
    tail = location.rsplit(",", 1)[-1] if "," in location else location
    postal, district = parse_postal_district(tail.strip())
    return address, postal, district


def _main_energy_source(v: Any) -> str | None:
    """'unbekannt' is the source's placeholder, not a real source."""
    if not v:
        return None
    return None if str(v).strip().lower() == "unbekannt" else str(v)


def _features(dump: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if dump.get("elevator"):
        out.append("Aufzug")
    if dump.get("balcony"):
        out.append("Balkon")
    if dump.get("basement"):
        out.append("Keller")
    heating = dump.get("heating")
    if heating:
        out.append(str(heating))
    return out


def to_listing_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a bronze raw_listings row to silver listings column values.

    `raw` is a dict with at least keys `source_name`, `external_id`,
    `scraped_at`, `data` (the original scraped record). The listing payload
    lives at `data.dump`.
    """
    record = raw["data"]
    dump = record.get("dump") or {}

    address, postal, district = _location_parts(dump.get("location"))

    return {
        "external_object_id": dump.get("objectId")
        or (str(dump["id"]) if dump.get("id") is not None else None),
        "listing_url": dump.get("url") or dump.get("scrapeUrl"),
        "title": dump.get("title"),
        "headline": dump.get("headline"),
        "description": None,
        "rooms": _num(dump.get("rooms")),
        "bedrooms": None,
        "bathrooms": None,
        "area_sqm": parse_sqm(dump.get("areaSqm")),
        "apartment_type": None,
        "cold_rent_eur": _num(dump.get("coldRentEur")),
        "warm_rent_eur": _num(dump.get("warmRentEur")),
        "nebenkosten_eur": _num(dump.get("nebenkostenEur")),
        # Mirror the all-in (warm) figure for cross-source consistency; the
        # source's Bruttokaltmiete is preserved in key_facts.rentGrossEur.
        "rent_gross_eur": _num(dump.get("warmRentEur")),
        "kaution_eur": None,
        "address": address,
        "postal_code": postal,
        "district": district,
        "city": "Berlin",
        # No coordinates on the cards -> NULL -> skips gold enrichment.
        "latitude": None,
        "longitude": None,
        "floor": parse_int_str(dump.get("floor")),
        # floorsTotal == 0 is the source's "unknown" sentinel.
        "floors_total": _positive(dump.get("floorsTotal")),
        "construction_year": parse_int_str(dump.get("constructionYear")),
        "available_from": parse_german_date(dump.get("occupationDate")),
        "available_until": None,
        "min_stay_months": None,
        "max_stay_months": None,
        "heating": dump.get("heating"),
        "main_energy_source": _main_energy_source(dump.get("mainEnergySource")),
        "energy_consumption_kwh": _positive(dump.get("energyConsumptionKwh")),
        "final_energy_value_kwh": _positive(dump.get("finalEnergyValueKwh")),
        "energy_pass_type": dump.get("energyPassType"),
        # The card exposes only elevator / balcony / basement as flags.
        "is_furnished": None,
        "has_kitchen": None,
        "has_bathroom": None,
        "has_elevator": bool(dump.get("elevator")),
        "has_balcony": bool(dump.get("balcony")),
        "has_terrace": None,
        "has_garden": None,
        "has_basement": bool(dump.get("basement")),
        # NOT NULL in listings; the card reports WBS status explicitly.
        "wbs_required": bool(dump.get("wbsRequired")),
        # inberlinwohnen aggregates the municipal housing companies.
        "lister_type": "commercial" if dump.get("company") else None,
        "company_name": dump.get("company"),
        "company_website": dump.get("companyWebsite"),
        "features": _features(dump),
        "images": [],
        "key_facts": {
            "objectId": dump.get("objectId"),
            "occupationDate": dump.get("occupationDate"),
            "wbsText": dump.get("wbsText"),
            "rentGrossEur": dump.get("rentGrossEur"),
            "coldRent": dump.get("coldRent"),
            "warmRent": dump.get("warmRent"),
            "extraCostsText": dump.get("extraCostsText"),
            "page": dump.get("page"),
        },
    }
