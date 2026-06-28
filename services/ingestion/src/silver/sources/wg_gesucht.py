"""WG-Gesucht bronze→silver transformer.

Scoped to the wg-gesucht detail JSON shape. The single public function is
`to_listing_row(raw)` which returns a dict of column values ready to upsert
into the `listings` table.
"""

from __future__ import annotations

from typing import Any

from .common import (
    amenity_match,
    clean_berlin_coords,
    find_amenity,
    map_lister_type,
    parse_energy_label,
    parse_floor_label,
    parse_german_date,
)


def _amenity_labels(amenities: list[dict] | None) -> list[str]:
    if not amenities:
        return []
    return [a.get("label", "") for a in amenities if a.get("label")]


def _images(images: list[dict] | None) -> list[str]:
    if not images:
        return []
    out: list[str] = []
    for img in images:
        url = img.get("large") or img.get("sized") or img.get("thumb")
        if url:
            out.append(url)
    return out


def _description(descriptions: list[dict] | None) -> str | None:
    if not descriptions:
        return None
    chunks: list[str] = []
    for d in descriptions:
        text = (d.get("text") or "").strip()
        if not text:
            continue
        tab = (d.get("tab") or "").strip()
        chunks.append(f"{tab}:\n{text}" if tab else text)
    return "\n\n".join(chunks) if chunks else None


def _floor_from_amenities(labels: list[str]) -> int | None:
    """WG amenity labels include floor markers like '5. OG' / 'EG' / 'Dachgeschoss'."""
    for label in labels:
        parsed = parse_floor_label(label)
        if parsed is not None:
            return parsed
    return None


def _apartment_type_from_amenities(labels: list[str]) -> str | None:
    """Prefer the building-era descriptor (Altbau/Neubau/sanierter Altbau)."""
    for needle in (
        "Sanierter Altbau",
        "sanierter Altbau",
        "Altbau",
        "Neubau",
        "Dachgeschoss",
    ):
        m = find_amenity(labels, needle)
        if m:
            return m
    return None


def _heating_from_amenities(labels: list[str]) -> str | None:
    for needle in (
        "Zentralheizung",
        "Gasheizung",
        "Fernwärme",
        "Ölheizung",
        "Ofenheizung",
        "Fußbodenheizung",
        "Etagenheizung",
    ):
        m = find_amenity(labels, needle)
        if m:
            return m
    return None


def _energy_fields(labels: list[str]) -> dict:
    """Find the energy-pass amenity (the long comma-separated one) and parse it."""
    for label in labels:
        if (
            "Energieeffizienzklasse" in label
            or "Bedarfsausweis" in label
            or "Verbrauchsausweis" in label
        ):
            return parse_energy_label(label)
    return {}


def to_listing_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a bronze raw_listings row to silver listings column values.

    `raw` is a dict with at least keys `source_name`, `external_id`,
    `scraped_at`, `data` (the original scraped record).
    """
    record = raw["data"]
    dump = record.get("dump") or {}

    amenity_labels = _amenity_labels(dump.get("amenities"))
    address = dump.get("address") or {}
    price = dump.get("price") or {}
    avail = dump.get("availability") or {}
    geo = dump.get("geo") or {}
    lister = dump.get("lister") or {}

    energy = _energy_fields(amenity_labels)

    return {
        "external_object_id": dump.get("externalId") or dump.get("scrapedAdId"),
        "listing_url": dump.get("canonicalUrl") or dump.get("url"),
        "title": dump.get("title"),
        "headline": None,
        "description": _description(dump.get("descriptions")),
        # WG-Gesucht's bronze blob often leaves the top-level `rooms` null and
        # carries the value under `dump.card.rooms` (the search-card payload
        # that came in via iron). Fall back so 60+ listings stop reading null.
        "rooms": dump.get("rooms") or (dump.get("card") or {}).get("rooms"),
        "bedrooms": None,
        "bathrooms": None,
        "area_sqm": float(dump["areaSqm"]) if dump.get("areaSqm") is not None else None,
        "apartment_type": _apartment_type_from_amenities(amenity_labels),
        "cold_rent_eur": price.get("kaltmieteEur"),
        "warm_rent_eur": price.get("warmmieteEur"),
        "nebenkosten_eur": price.get("nebenkostenEur"),
        "rent_gross_eur": price.get("warmmieteEur"),
        "kaution_eur": price.get("kautionEur"),
        "address": address.get("raw") or address.get("street"),
        "postal_code": address.get("postalCode"),
        "district": address.get("district"),
        "city": address.get("city"),
        # Validated through clean_berlin_coords so 0/0 and out-of-Berlin
        # sentinels become NULL instead of polluting geo-context queries.
        **dict(
            zip(
                ("latitude", "longitude"),
                clean_berlin_coords(
                    geo.get("lat") if geo else None,
                    geo.get("lng") if geo else None,
                ),
                strict=True,
            )
        ),
        "floor": _floor_from_amenities(amenity_labels),
        "floors_total": None,
        "construction_year": energy.get("construction_year"),
        "available_from": parse_german_date(avail.get("from")),
        "available_until": parse_german_date(avail.get("until")),
        "min_stay_months": avail.get("minStayMonths"),
        "max_stay_months": avail.get("maxStayMonths"),
        "heating": _heating_from_amenities(amenity_labels),
        "main_energy_source": energy.get("main_energy_source"),
        "energy_consumption_kwh": energy.get("energy_consumption_kwh"),
        "final_energy_value_kwh": None,
        "energy_pass_type": energy.get("energy_pass_type"),
        "is_furnished": amenity_match(amenity_labels, "möbliert", "moebliert"),
        "has_kitchen": amenity_match(
            amenity_labels, "Eigene Küche", "Kochnische", "Einbauküche"
        ),
        "has_bathroom": amenity_match(
            amenity_labels, "Eigenes Bad", "Dusche", "Badewanne"
        ),
        "has_elevator": amenity_match(amenity_labels, "Aufzug"),
        "has_balcony": amenity_match(amenity_labels, "Balkon"),
        "has_terrace": amenity_match(amenity_labels, "Terrasse"),
        "has_garden": amenity_match(amenity_labels, "Garten"),
        "has_basement": amenity_match(amenity_labels, "Keller", "Fahrradkeller"),
        "wbs_required": amenity_match(amenity_labels, "WBS", "Wohnberechtigungsschein"),
        "lister_type": map_lister_type(lister.get("type")),
        "company_name": None,
        "company_website": None,
        "features": amenity_labels,
        "images": _images(dump.get("images")),
        "key_facts": dump.get("keyFacts"),
    }
