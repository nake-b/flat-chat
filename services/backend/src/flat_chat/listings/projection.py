"""Tier-2 card projection — the single SELECT-row → `ListingCard` mapping.

Lives in `listings/` (the leaf domain) so BOTH `search.SearchService` (the
preview slice) and `listings.ListingService.get_cards` (lazy hydration)
project a card row the same way — one definition, no drift. Gold stores raw
values; the bucket labels are applied here at projection time, so threshold
tweaks don't need a gold rebuild.
"""

from __future__ import annotations

from .context import ListingCard
from .labels import (
    bucket_density,
    bucket_noise,
    primary_transit_line,
    walk_minutes,
)
from .models import Listing, ListingGeoContext

# Columns a card row must SELECT: the Listing entity + the chip scalars off
# `listings_geo_context`. Both the preview query and the `?view=card` batch
# read select exactly these (in this order) so `row_to_listing_card` can read
# them by name from the row mapping.
CARD_COLUMNS = (
    Listing,
    ListingGeoContext.nearest_transit_lines,
    ListingGeoContext.nearest_transit_m,
    ListingGeoContext.nearest_transit_name,
    ListingGeoContext.nearest_park_name,
    ListingGeoContext.nearest_park_m,
    ListingGeoContext.noise_total_lden,
    ListingGeoContext.persons_per_hectare,
    ListingGeoContext.inside_ring,
    ListingGeoContext.listing_bezirk,
    ListingGeoContext.listing_ortsteil,
)


def row_to_listing_card(row, *, with_score: bool) -> ListingCard:
    """Build a ListingCard from a SELECT row shaped like `CARD_COLUMNS`
    (optionally + a trailing `similarity_score` column when `with_score`)."""
    listing: Listing = row[0]
    mapping = row._mapping

    nearest_transit_lines = mapping.get("nearest_transit_lines")
    # Prefer rail (U/S) over tram/bus rather than taking the array's first
    # element — a stop serving both a bus and a U-Bahn should surface the U-Bahn.
    nearest_transit_line = primary_transit_line(nearest_transit_lines)
    nearest_transit_m = mapping.get("nearest_transit_m")
    noise_lden = mapping.get("noise_total_lden")
    pph = mapping.get("persons_per_hectare")

    # Pick the first image URL if any (browser handles the rest via HTTP
    # detail fetch; the card just needs a thumbnail).
    image_url: str | None = None
    if listing.images:
        for item in listing.images:
            if isinstance(item, str):
                image_url = item
                break
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                image_url = item["url"]
                break

    sim_score = None
    if with_score and "similarity_score" in mapping:
        # Postgres cosine_distance returns 0..2; convert to 0..1 similarity
        raw = mapping["similarity_score"]
        if raw is not None:
            sim_score = round(1 - float(raw), 4)

    return ListingCard(
        id=str(listing.id),
        lat=listing.latitude,
        lng=listing.longitude,
        price_warm_eur=listing.warm_rent_eur,
        price_cold_eur=listing.cold_rent_eur,
        nebenkosten_eur=listing.nebenkosten_eur,
        kaution_eur=listing.kaution_eur,
        rooms=listing.rooms,
        bedrooms=listing.bedrooms,
        area_sqm=listing.area_sqm,
        floor=listing.floor,
        floors_total=listing.floors_total,
        available_from=(
            listing.available_from.isoformat() if listing.available_from else None
        ),
        listing_type=listing.apartment_type,
        district=listing.district,
        title=listing.title,
        address=listing.address,
        wbs_required=listing.wbs_required,
        is_furnished=listing.is_furnished,
        has_balcony=listing.has_balcony,
        has_kitchen=listing.has_kitchen,
        has_elevator=listing.has_elevator,
        has_garden=listing.has_garden,
        heating=listing.heating,
        energy_consumption_kwh=listing.energy_consumption_kwh,
        lister_type=listing.lister_type,
        source_url=listing.listing_url,
        image_url=image_url,
        nearest_transit_line=nearest_transit_line,
        walk_min_to_transit=walk_minutes(nearest_transit_m),
        nearest_park_name=mapping.get("nearest_park_name"),
        nearest_park_m=mapping.get("nearest_park_m"),
        noise_label=bucket_noise(noise_lden),
        density_label=bucket_density(pph),
        inside_ring=mapping.get("inside_ring"),
        listing_bezirk=mapping.get("listing_bezirk"),
        listing_ortsteil=mapping.get("listing_ortsteil"),
        similarity_score=sim_score,
    )
