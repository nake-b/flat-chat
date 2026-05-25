"""Kleinanzeigen bronze→silver transformer.

Scoped to the kleinanzeigen detail JSON shape. The single public function is
`to_listing_row(raw)` which returns a dict of column values ready to upsert
into the `listings` table.
"""

from __future__ import annotations

from typing import Any

from .common import (
    amenity_match,
    map_lister_type,
    parse_german_month_year,
    parse_int_str,
    parse_postal_district,
    parse_sqm,
)


def _images(images: list | None) -> list[str]:
    if not images:
        return []
    out: list[str] = []
    for img in images:
        if isinstance(img, str):
            out.append(img)
        elif isinstance(img, dict):
            url = img.get("url") or img.get("large") or img.get("sized")
            if url:
                out.append(url)
    return out


def _features(features: list | None) -> list[str]:
    if not features:
        return []
    return [str(f) for f in features if f]


def to_listing_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a bronze raw_listings row to silver listings column values."""
    record = raw["data"]
    dump = record.get("dump") or {}

    details = dump.get("details") or {}
    price = dump.get("price") or {}
    geo = dump.get("geo") or {}
    seller = dump.get("seller") or {}
    features = _features(dump.get("features"))

    postal, district = parse_postal_district(dump.get("locality"))

    return {
        "external_object_id": dump.get("externalId") or dump.get("scrapedAdId"),
        "listing_url": dump.get("canonicalUrl") or dump.get("url"),

        "title": dump.get("title"),
        "headline": None,
        "description": dump.get("description"),
        "rooms": parse_int_str(details.get("zimmer")),
        "bedrooms": parse_int_str(details.get("schlafzimmer")),
        "bathrooms": parse_int_str(details.get("badezimmer")),
        "area_sqm": parse_sqm(details.get("wohnflaeche")),
        "apartment_type": details.get("wohnungstyp"),

        "cold_rent_eur": price.get("coldRentEur") or price.get("kaltmieteEur"),
        "warm_rent_eur": price.get("warmmieteEur"),
        "nebenkosten_eur": price.get("nebenkostenEur"),
        "rent_gross_eur": price.get("warmmieteEur"),
        "kaution_eur": price.get("kautionEur"),

        "address": dump.get("locality"),
        "postal_code": postal,
        "district": district,
        "city": "Berlin",
        "latitude": geo.get("lat") if geo else None,
        "longitude": geo.get("lng") if geo else None,

        "floor": parse_int_str(details.get("etage")),
        "floors_total": None,
        "construction_year": None,
        "available_from": parse_german_month_year(details.get("verfuegbarAb")),
        "available_until": None,
        "min_stay_months": None,
        "max_stay_months": None,

        "heating": None,
        "main_energy_source": None,
        "energy_consumption_kwh": None,
        "final_energy_value_kwh": None,
        "energy_pass_type": None,

        "is_furnished": amenity_match(features, "möbliert", "moebliert"),
        "has_kitchen": amenity_match(features, "Einbauküche", "Pantryküche"),
        "has_bathroom": amenity_match(features, "Dusche", "Badewanne"),
        "has_elevator": amenity_match(features, "Aufzug"),
        "has_balcony": amenity_match(features, "Balkon"),
        "has_terrace": amenity_match(features, "Terrasse"),
        "has_garden": amenity_match(features, "Garten"),
        "has_basement": amenity_match(features, "Keller"),
        "wbs_required": amenity_match(features, "WBS", "Wohnberechtigungsschein"),

        "lister_type": map_lister_type(seller.get("type")) if seller else None,
        "company_name": None,
        "company_website": None,

        "features": features,
        "images": _images(dump.get("images")),
        "key_facts": None,
    }
