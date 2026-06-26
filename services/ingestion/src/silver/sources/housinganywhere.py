"""HousingAnywhere bronze→silver transformer.

Scoped to the housinganywhere detail JSON shape. The single public function is
`to_listing_row(raw)` which returns a dict of column values ready to upsert
into the `listings` table.

The bronze dump's primary payload is `dump.entity` — the page's
`window.__PRELOADED_STATE__.listing.entity`. Money values there (`price`,
`costs.costs.*.value`) are EURO CENTS and are divided by 100 here; the
search-card payload (`dump.card.priceEur`, from microdata) is already whole
euros and must never be divided. Fallback-tier rows (`entity` missing) map
from the LD+JSON Accommodation block + og: meta + the card.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import (
    clean_berlin_coords,
    map_lister_type,
    parse_float_str,
    parse_int_str,
    parse_sqm,
)


def _cents(v: int | float | None) -> float | None:
    """86500 (euro cents) -> 865.0. None-safe."""
    if isinstance(v, (int, float)):
        return v / 100.0
    return None


def _fac_bool(fac: dict, *keys: str) -> bool | None:
    """Facility values: 'yes'/'private'/'shared' -> True, 'no' -> False,
    None/'' -> None. With several keys, the first decisive value wins."""
    for key in keys:
        v = fac.get(key)
        if v in (None, ""):
            continue
        # First key with a decisive value wins.
        return str(v).strip().lower() != "no"
    return None


def _parse_iso(s: str | None) -> datetime | None:
    """'2026-08-01T00:00:00Z' -> datetime. The year-9999 open-ended sentinel
    maps to None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.year >= 9999:
        return None
    return dt


def _images(photo_list: list[dict] | None) -> list[str]:
    """photoURLList items carry full imgix URLs — no prefixing needed."""
    if not photo_list:
        return []
    out: list[str] = []
    for photo in photo_list:
        if isinstance(photo, str):
            out.append(photo)
            continue
        url = photo.get("url") or photo.get("large") or photo.get("src")
        if url:
            out.append(url)
    return out


# Facility keys that are measurements mapped to dedicated columns, not features.
_NON_FEATURE_KEYS = {"bedroom_count", "bathroom_count", "bedroom_size", "total_size"}


def _features(fac: dict) -> list[str]:
    """'yes' values become the bare key ('wifi'); descriptive values keep both
    ('bathroom: private'); 'no'/None/measurement keys are dropped."""
    out: list[str] = []
    for k, v in fac.items():
        if k in _NON_FEATURE_KEYS or v in (None, ""):
            continue
        s = str(v).strip().lower()
        if s == "no":
            continue
        out.append(k if s == "yes" else f"{k}: {v}")
    return sorted(out)


def _availability(entity: dict) -> tuple[datetime | None, datetime | None]:
    """Earliest bookable period -> (available_from, available_until)."""
    periods = entity.get("bookablePeriods") or []
    parsed = []
    for p in periods:
        start = _parse_iso(p.get("from"))
        if start is not None:
            parsed.append((start, _parse_iso(p.get("to"))))
    if not parsed:
        return None, None
    parsed.sort(key=lambda pair: pair[0])
    return parsed[0]


def to_listing_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a bronze raw_listings row to silver listings column values.

    `raw` is a dict with at least keys `source_name`, `external_id`,
    `scraped_at`, `data` (the original scraped record).
    """
    record = raw["data"]
    dump = record.get("dump") or {}

    entity = dump.get("entity") or {}
    fac = entity.get("facilities") or {}
    costs = (entity.get("costs") or {}).get("costs") or {}
    card = dump.get("card") or {}
    ld = dump.get("ldjson") or {}
    og = dump.get("ogMeta") or {}
    adv = dump.get("advertiser") or {}
    ld_geo = ld.get("geo") or {}

    available_from, available_until = _availability(entity)

    bedrooms = parse_int_str(fac.get("bedroom_count"))
    # bedroom_count "0" means studio — one room, zero separate bedrooms.
    if bedrooms is None:
        rooms = None
    elif bedrooms == 0:
        rooms = 1.0
    else:
        rooms = float(bedrooms)

    if entity:
        warm_rent = _cents(entity.get("price"))
    else:
        # Fallback tier: the card's microdata price is already whole euros.
        warm_rent = card.get("priceEur")

    features = _features(fac) + [b for b in card.get("badges") or [] if b]

    return {
        "external_object_id": (
            str(entity["id"]) if entity.get("id") else dump.get("externalId")
        ),
        "listing_url": dump.get("canonicalUrl") or dump.get("url"),

        "title": ld.get("name") or og.get("og:title") or card.get("title"),
        "headline": None,
        "description": entity.get("description")
        or (entity.get("property") or {}).get("description")
        or ld.get("description")
        or og.get("og:description"),
        "rooms": rooms,
        "bedrooms": bedrooms,
        "bathrooms": parse_int_str(fac.get("bathroom_count")),
        "area_sqm": parse_sqm(fac.get("total_size")),
        "apartment_type": "studio" if str(fac.get("bedroom_count")) == "0" else None,

        # HousingAnywhere advertises one all-inclusive monthly figure; which
        # bills are included is flagged in the facilities map, not split out.
        "cold_rent_eur": None,
        "warm_rent_eur": warm_rent,
        "nebenkosten_eur": None,
        "rent_gross_eur": warm_rent,
        "kaution_eur": _cents((costs.get("security-deposit") or {}).get("value")),

        "address": entity.get("street"),
        "postal_code": entity.get("postalCode"),
        "district": None,
        "city": entity.get("city") or "Berlin",
        # Validated through clean_berlin_coords so 0/0 and out-of-Berlin
        # sentinels become NULL instead of polluting geo-context queries.
        **dict(zip(
            ("latitude", "longitude"),
            clean_berlin_coords(
                entity.get("latitude") or parse_float_str(ld_geo.get("latitude")),
                entity.get("longitude") or parse_float_str(ld_geo.get("longitude")),
            ),
        )),

        "floor": None,
        "floors_total": None,
        "construction_year": None,
        "available_from": available_from,
        "available_until": available_until,
        "min_stay_months": entity.get("minimumStayMonths"),
        # maxBookableDays is a booking-window setting, not a max stay — keep
        # it in key_facts only.
        "max_stay_months": None,

        "heating": fac.get("heating"),
        "main_energy_source": None,
        "energy_consumption_kwh": None,
        "final_energy_value_kwh": None,
        "energy_pass_type": None,

        "is_furnished": _fac_bool(fac, "bedroom_furnished", "furniture"),
        "has_kitchen": _fac_bool(fac, "kitchen"),
        "has_bathroom": _fac_bool(fac, "bathroom"),
        "has_elevator": _fac_bool(fac, "elevator"),
        "has_balcony": _fac_bool(fac, "balcony_terrace"),
        "has_terrace": _fac_bool(fac, "balcony_terrace"),
        "has_garden": _fac_bool(fac, "garden"),
        "has_basement": _fac_bool(fac, "basement"),
        # NOT NULL in listings. HousingAnywhere is a furnished mid-term
        # platform — WBS (social housing) listings don't appear there.
        "wbs_required": False,

        "lister_type": map_lister_type(adv.get("type")),
        "company_name": None,
        "company_website": None,

        "features": features,
        "images": _images(entity.get("photoURLList")) or card.get("imageUrls") or [],
        "key_facts": {
            "facilities": fac or None,
            "costs": costs or None,
            "currency": entity.get("currency"),
            "freePlaces": entity.get("freePlaces"),
            "isMultiUnit": entity.get("isMultiUnit"),
            "minimumStayMonths": entity.get("minimumStayMonths"),
            "maxBookableDays": entity.get("maxBookableDays"),
            "registrationPossible": fac.get("registration_possible"),
            "overallRating": dump.get("overallRating"),
            "cardPriceLabel": card.get("priceLabel"),
            "extractionTier": dump.get("extractionTier"),
        },
    }
