"""NULL-handling regression suite for `SearchService` geo filters.

`test_search_service.py` covers the happy paths — seed a row that matches,
assert it comes back. This file covers the *other* axis: a listing has a
gold row, but one geo column is NULL (the gold ETL couldn't resolve a
value for that source — e.g. listing outside Berlin's noise coverage
polygon).

Today's contract: each geo filter is a strict B-tree predicate on the
relevant gold scalar. A NULL on that scalar makes the predicate NULL → the
row is dropped. If anyone ever rewrites this to "treat NULL as optimistic
pass", these tests fail loudly.

Why this matters: a silent "NULL passes" refactor would resurface garbage
listings (e.g. miscoded coords with no resolvable noise sample) in
constrained searches, eroding the user's trust in the filter set.
"""

from __future__ import annotations

from flat_chat.search.geo_filters import TransitFilter
from flat_chat.search.schemas import SearchParams

from ..conftest import DB_REQUIRED
from ..fixtures.factories import drive_search as _drive
from ..fixtures.factories import gold_row as _gold_row
from ..fixtures.factories import listing_row as _listing_row

pytestmark = DB_REQUIRED


def test_null_noise_total_lden_drops_listing_from_quiet_filter(async_db_url):
    """Gold row exists but `noise_total_lden` is NULL. `max_noise=quiet`
    must NOT return the listing — a strict `<` against NULL is NULL."""
    with_noise = _listing_row()
    no_noise = _listing_row()
    seeds = [
        (with_noise, _gold_row(with_noise["id"], noise_total_lden=45.0)),
        (no_noise, _gold_row(no_noise["id"], noise_total_lden=None)),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(max_noise="quiet"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(with_noise["id"]) in ids
    assert str(no_noise["id"]) not in ids


def test_null_density_drops_listing_from_sparse_filter(async_db_url):
    sparse = _listing_row()
    null_density = _listing_row()
    seeds = [
        (sparse, _gold_row(sparse["id"], persons_per_hectare=40.0)),
        (null_density, _gold_row(null_density["id"], persons_per_hectare=None)),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(density="sparse"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(sparse["id"]) in ids
    assert str(null_density["id"]) not in ids


def test_null_transit_distance_drops_listing_from_transit_filter(async_db_url):
    """`nearest_transit_m IS NULL` → strict `<= near` returns NULL → row
    drops. Notably the service applies an explicit `.is_not(None)`
    guard (see `_apply_transit_filter`); without it, asyncpg can still
    short-circuit a NULL comparison to NULL but explicitness matters."""
    near = _listing_row()
    null_transit = _listing_row()
    seeds = [
        (near, _gold_row(near["id"], nearest_transit_m=200)),
        (null_transit, _gold_row(null_transit["id"], nearest_transit_m=None)),
    ]

    async def body(service):
        params = SearchParams(transit=TransitFilter(distance="near"))
        results, _ = await service.search(params)
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids
    assert str(null_transit["id"]) not in ids


def test_null_park_distance_drops_listing_from_near_park_filter(async_db_url):
    """The `_apply_geo_context_filters` near_park branch explicitly
    layers `nearest_park_m IS NOT NULL` alongside the threshold compare
    — confirm that guard does what it says."""
    near = _listing_row()
    null_park = _listing_row()
    seeds = [
        (near, _gold_row(near["id"], nearest_park_m=200)),
        (null_park, _gold_row(null_park["id"], nearest_park_m=None)),
    ]

    async def body(service):
        results, _ = await service.search(SearchParams(near_park="near"))
        return {r.id for r in results}

    ids = _drive(async_db_url, seeds, body)
    assert str(near["id"]) in ids
    assert str(null_park["id"]) not in ids
