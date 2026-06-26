"""Integration tests for `ListingService.get(id)`.

This service is shared between the agent's `open_listing` tool and the
HTTP `GET /api/listings/{id}` route. The contract is narrow but
load-bearing:

  - Listing + gold both present → fully populated ListingDetail with
    bucket labels applied at projection time.
  - Listing present, no gold row → tier-2 fields only, all `nearest_*`
    + profile fields stay default (the LEFT OUTER JOIN branch).
  - Unknown UUID → None (HTTP route surfaces 404).
  - Non-UUID input → None via `uuid.UUID(...)` ValueError swallow.
  - `get(uuid_obj)` and `get(str(uuid_obj))` agree (the coercion contract).
"""

from __future__ import annotations

import uuid

from flat_chat.listings.models import (
    ListingNearbyHospital,
    ListingNearbyKita,
    ListingNearbyLandmark,
    ListingNearbyPark,
    ListingNearbyPlayground,
    ListingNearbySchool,
    ListingNearbyTransit,
    ListingNearbyWater,
)
from flat_chat.listings.service import ListingService

from ..conftest import DB_REQUIRED
from ..fixtures.factories import (
    gold_row as _gold_row,
)
from ..fixtures.factories import (
    listing_row as _listing_row,
)
from ..fixtures.factories import (
    nearby_hospital_row,
    nearby_kita_row,
    nearby_landmark_row,
    nearby_park_row,
    nearby_playground_row,
    nearby_school_row,
    nearby_transit_row,
    nearby_water_row,
    with_session,
)

pytestmark = DB_REQUIRED


def test_get_returns_detail_with_full_geo_context(async_db_url):
    """Happy path — every JSONB blob populated, labels applied."""
    listing = _listing_row(
        title="Bright 2-room",
        warm_rent_eur=1500.0,
        cold_rent_eur=1100.0,
        rooms=2.0,
        area_sqm=55.0,
        district="Kreuzberg",
        address="Manteuffelstr. 1",
        images=["https://example.org/a.jpg", {"url": "https://example.org/b.jpg"}],
    )
    gold = _gold_row(
        listing["id"],
        nearest_transit_m=200,
        nearest_transit_lines=["U1", "U8"],
        nearest_transit_name="U Kottbusser Tor",
        school_catchment={"school_name": "GS Lenau"},
        noise_profile={"total_lden": 60.0},
        greenery_profile={"green_m2_within_300m": 6000.0},
        density_profile={
            "persons_per_hectare": 200.0,
            "population": 12000,
        },
        noise_total_lnight=52.0,
        inside_ring=True,
        listing_bezirk="Friedrichshain-Kreuzberg",
        listing_ortsteil="Kreuzberg",
        disabled_parking_count=3,
    )
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"],
                stop_id="900100001",
                name="U Kottbusser Tor",
                modes=[400],
                lines=["U1", "U8"],
                distance_m=200,
            ),
        ),
        (
            ListingNearbySchool,
            nearby_school_row(
                listing["id"],
                name="GS Lenau",
                school_type="Grundschule",
                distance_m=300,
            ),
        ),
        (
            ListingNearbyPark,
            nearby_park_row(listing["id"], name="Görlitzer Park", distance_m=400),
        ),
        (
            ListingNearbyPlayground,
            nearby_playground_row(listing["id"], name="Mariannenplatz", distance_m=250),
        ),
        (
            ListingNearbyHospital,
            nearby_hospital_row(
                listing["id"], name="Urban", tier="plan_hospital", distance_m=900
            ),
        ),
        (
            ListingNearbyWater,
            nearby_water_row(
                listing["id"], name="Landwehrkanal", water_kind="canal", distance_m=500
            ),
        ),
        (
            ListingNearbyKita,
            nearby_kita_row(listing["id"], name="Kita Sonnenschein", distance_m=180),
        ),
        (
            ListingNearbyLandmark,
            nearby_landmark_row(
                listing["id"], name="Oberbaumbrücke", category="bridge", distance_m=650
            ),
        ),
    ]

    async def body(session):
        return await ListingService(session).get_detail(listing["id"])

    detail = with_session(async_db_url, [(listing, gold)], body, junctions=junctions)

    assert detail is not None
    # Listing-tier fields project through directly.
    assert detail.id == str(listing["id"])
    assert detail.title == "Bright 2-room"
    assert detail.price_warm_eur == 1500.0
    assert detail.rooms == 2.0
    assert detail.district == "Kreuzberg"
    # Images get flattened — both string and {"url": ...} survive.
    assert detail.images == [
        "https://example.org/a.jpg",
        "https://example.org/b.jpg",
    ]
    # Geo-context: transit top-3 decoded to English mode labels and
    # walk_minutes is computed at parse time (200m / 1.4 m/s ≈ 2.4 min).
    assert len(detail.nearest_transit_stops) == 1
    stop = detail.nearest_transit_stops[0]
    assert stop.name == "U Kottbusser Tor"
    assert stop.modes == ["u_bahn"]
    assert stop.distance_m == 200
    assert stop.walk_minutes == 2
    # School / park / playground / hospital / water all populated.
    assert detail.school_catchment is not None
    assert detail.school_catchment.school_name == "GS Lenau"
    assert detail.nearest_schools[0].name == "GS Lenau"
    assert detail.nearest_parks[0].name == "Görlitzer Park"
    assert detail.nearest_playground is not None
    assert detail.nearest_playground.distance_m == 250
    assert detail.nearest_hospitals[0].tier == "plan_hospital"
    assert detail.nearest_water is not None
    assert detail.nearest_water.water_kind == "canal"
    # Profile labels applied from raw values via `listings.labels` —
    # 60 dB is in the "lively" band (>= 55, < 65).
    assert detail.noise is not None
    assert detail.noise.label == "lively"
    assert detail.noise.total_lden == 60.0
    # Lnight is the scalar off the gold column (detail-only), not the blob.
    assert detail.noise.total_lnight == 52.0
    # 6000 m² green is "leafy" (>= 5000, < 10000).
    assert detail.greenery is not None
    assert detail.greenery.label == "leafy"
    # 200 ppH is "dense" (>= 150).
    assert detail.density is not None
    assert detail.density.label == "dense"
    # Kitas + landmarks come from the new junction tables.
    assert detail.nearest_kitas[0].name == "Kita Sonnenschein"
    assert detail.nearest_landmarks[0].name == "Oberbaumbrücke"
    assert detail.nearest_landmarks[0].category == "bridge"
    # Admin-area scalars off the gold row.
    assert detail.inside_ring is True
    assert detail.listing_bezirk == "Friedrichshain-Kreuzberg"
    assert detail.listing_ortsteil == "Kreuzberg"
    assert detail.disabled_parking_count == 3


def test_get_caps_transit_at_top_n_in_rank_order(async_db_url):
    """Single json_agg query honours the top-N cap + `ORDER BY rank`.

    Seed 5 transit rows (ranks 1..5, distinct distances). `get()` must
    return exactly `_TRANSIT_TOP_N` of them, ascending by rank.
    """
    from flat_chat.listings.service import _TRANSIT_TOP_N

    listing = _listing_row(title="Transit-rich")
    gold = _gold_row(listing["id"])
    junctions = [
        (
            ListingNearbyTransit,
            nearby_transit_row(
                listing["id"],
                stop_id=f"stop-{rank}",
                name=f"Stop {rank}",
                distance_m=100 * rank,
                rank=rank,
            ),
        )
        for rank in range(1, 6)
    ]

    async def body(session):
        return await ListingService(session).get_detail(listing["id"])

    detail = with_session(async_db_url, [(listing, gold)], body, junctions=junctions)

    assert detail is not None
    stops = detail.nearest_transit_stops
    assert len(stops) == _TRANSIT_TOP_N
    # Ascending by rank → distances 100, 200, 300 (ranks 1, 2, 3).
    assert [s.distance_m for s in stops] == [100, 200, 300]
    assert [s.name for s in stops] == ["Stop 1", "Stop 2", "Stop 3"]


def test_get_returns_tier2_only_when_no_gold_row(async_db_url):
    """LEFT OUTER JOIN branch — listing exists, gold row missing.

    Returned detail must still carry the listing-tier fields and leave
    every geo-context field at its Pydantic default.
    """
    listing = _listing_row(title="Unenriched", warm_rent_eur=900.0)

    async def body(session):
        return await ListingService(session).get_detail(listing["id"])

    detail = with_session(async_db_url, [(listing, None)], body)

    assert detail is not None
    assert detail.id == str(listing["id"])
    assert detail.title == "Unenriched"
    assert detail.price_warm_eur == 900.0
    # Geo-context fields stay at defaults — no NoneType errors, no partial fill.
    assert detail.nearest_transit_stops == []
    assert detail.school_catchment is None
    assert detail.nearest_schools == []
    assert detail.nearest_parks == []
    assert detail.nearest_playground is None
    assert detail.nearest_hospitals == []
    assert detail.nearest_water is None
    assert detail.nearest_kitas == []
    assert detail.nearest_landmarks == []
    assert detail.noise is None
    assert detail.greenery is None
    assert detail.density is None
    assert detail.inside_ring is None
    assert detail.disabled_parking_count == 0


def test_get_returns_none_for_unknown_uuid(async_db_url):
    """Valid-shape UUID with no matching row → None (HTTP surfaces 404)."""

    async def body(session):
        return await ListingService(session).get_detail(uuid.uuid4())

    assert with_session(async_db_url, [], body) is None


def test_get_returns_none_for_invalid_uuid(async_db_url):
    """Non-UUID input → None (the ValueError swallow path)."""

    async def body(session):
        return await ListingService(session).get_detail("not-a-uuid")

    assert with_session(async_db_url, [], body) is None


def test_get_accepts_both_uuid_and_string(async_db_url):
    """The id-coercion contract: caller may pass UUID or str."""
    listing = _listing_row(title="Coerce me")

    async def body(session):
        service = ListingService(session)
        via_uuid = await service.get_detail(listing["id"])
        via_str = await service.get_detail(str(listing["id"]))
        return via_uuid, via_str

    via_uuid, via_str = with_session(async_db_url, [(listing, None)], body)

    assert via_uuid is not None
    assert via_str is not None
    assert via_uuid.id == via_str.id
    assert via_uuid.title == via_str.title == "Coerce me"


# ---------------------------------------------------------------------------
# get_cards — tier-2 batch hydration (lazy-load / bookmarks accessor)
# ---------------------------------------------------------------------------


def test_get_cards_returns_cards_in_requested_order_dropping_unknown(async_db_url):
    l1 = _listing_row(title="First", warm_rent_eur=1000.0)
    l2 = _listing_row(title="Second", warm_rent_eur=2000.0)
    l3 = _listing_row(title="Third", warm_rent_eur=3000.0)
    seeds = [
        (l1, _gold_row(l1["id"])),
        (l2, _gold_row(l2["id"])),
        (l3, _gold_row(l3["id"])),
    ]

    async def body(session):
        service = ListingService(session)
        order = [
            str(l3["id"]),
            str(l1["id"]),
            "00000000-0000-0000-0000-000000000000",  # unknown → omitted
            str(l2["id"]),
        ]
        cards = await service.get_cards(order)
        return [c.id for c in cards], [c.title for c in cards]

    ids, titles = with_session(async_db_url, seeds, body)
    # Order follows the request, not the DB; the unknown id is dropped.
    assert ids == [str(l3["id"]), str(l1["id"]), str(l2["id"])]
    assert titles == ["Third", "First", "Second"]


def test_get_cards_empty_input_returns_empty(async_db_url):
    async def body(session):
        return await ListingService(session).get_cards([])

    assert with_session(async_db_url, [], body) == []
