"""NULL-handling regression suite for `SearchService` geo filters.

`test_search_service.py` covers the happy paths — seed a row that matches,
assert it comes back. This file covers the *other* axis: what happens when
the underlying gold value is NULL or the junction table has no row.

Today's contract per filter kind:

  - **POI filters** (transit / schools / hospitals / parks / playgrounds
    / water): EXISTS against the per-listing junction table. A listing
    with no junction rows for that family fails the EXISTS → row drops.
  - **Scalar / field filters** with strict comparison (`inside_ring =
    :v`, `density < cutoff`, `min_greenery >= cutoff`): NULL on the
    column makes the predicate NULL → row drops. Strict semantics.
  - **`max_noise`** — optimistic-include: `or_(IS NULL, < cutoff)`. A
    listing whose nearest noise sample is >50 m away (post-gate NULL)
    PASSES the "quiet" filter. We don't claim a listing is loud when we
    have no nearby reading. Locks the optimistic-include semantics so a
    future "drop the NULL branch for performance" refactor surfaces
    immediately.

Why this matters: silent semantics shifts on NULL rows are easy to
introduce and hard to spot in production until a user reports "why
isn't my listing showing up under filter X." These tests fail loudly
when that happens.
"""

from __future__ import annotations

from flat_chat.search.geo_filters import TransitFilter
from flat_chat.search.schemas import SearchParams

from ..conftest import DB_REQUIRED
from ..fixtures.factories import (
    drive_search as _drive,
)
from ..fixtures.factories import (
    gold_row as _gold_row,
)
from ..fixtures.factories import (
    listing_row as _listing_row,
)

pytestmark = DB_REQUIRED


def test_null_noise_optimistic_includes_listing_in_quiet_filter(async_db_url):
    """`noise_total_lden IS NULL` → PASSES `max_noise="quiet"`.

    Optimistic-include semantics: NULL means "no trusted noise reading
    within the 50 m gate (gold-side)", typically a listing with bad
    coordinates. We don't claim a listing is loud when we have no
    nearby sample; the predicate is `or_(IS NULL, < cutoff)`.
    """
    quiet = _listing_row()
    null_noise = _listing_row()
    loud = _listing_row()
    seeds = [
        (quiet, _gold_row(quiet["id"], noise_total_lden=45.0)),
        (null_noise, _gold_row(null_noise["id"], noise_total_lden=None)),
        (loud, _gold_row(loud["id"], noise_total_lden=70.0)),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(SearchParams(max_noise="quiet"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(quiet["id"]) in ids
    assert str(null_noise["id"]) in ids
    assert str(loud["id"]) not in ids


def test_null_density_drops_listing_from_sparse_filter(async_db_url):
    """`persons_per_hectare IS NULL` → strict `< 50` returns NULL → row drops."""
    sparse = _listing_row()
    null_density = _listing_row()
    seeds = [
        (sparse, _gold_row(sparse["id"], persons_per_hectare=40.0)),
        (null_density, _gold_row(null_density["id"], persons_per_hectare=None)),
    ]

    async def body(service):
        results, _preview, _, _ = await service.search(SearchParams(density="sparse"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(sparse["id"]) in ids
    assert str(null_density["id"]) not in ids


def test_missing_transit_junction_row_drops_listing_from_transit_filter(async_db_url):
    """A listing with NO `listings_nearby_transit` row fails EXISTS → drops."""
    no_transit = _listing_row()
    seeds = [(no_transit, _gold_row(no_transit["id"]))]

    async def body(service):
        params = SearchParams(transit=TransitFilter(distance="near"))
        results, _preview, _, _ = await service.search(params)
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body)
    assert str(no_transit["id"]) not in ids


def test_null_inside_ring_drops_listing_from_ring_filter(async_db_url):
    """`inside_ring = :v` against NULL returns NULL → row drops.

    Strict semantics: gold never tested this listing against the
    Umweltzone polygon, so we don't claim it is inside (or outside) the
    ring — it simply drops out of either-direction filter.
    """
    inside = _listing_row()
    null_ring = _listing_row()
    seeds = [
        (inside, _gold_row(inside["id"], inside_ring=True)),
        (null_ring, _gold_row(null_ring["id"], inside_ring=None)),
    ]

    async def body(service):
        params = SearchParams(inside_ring=True)
        results, _preview, _, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(inside["id"]) in ids
    assert str(null_ring["id"]) not in ids


def test_missing_park_junction_row_drops_listing_from_near_park_filter(async_db_url):
    """A listing with NO `listings_nearby_parks` row fails EXISTS → drops."""
    no_park = _listing_row()
    seeds = [(no_park, _gold_row(no_park["id"]))]

    async def body(service):
        results, _preview, _, _ = await service.search(SearchParams(near_park="near"))
        return [r.id for r in results]

    ids = _drive(async_db_url, seeds, body)
    assert str(no_park["id"]) not in ids
