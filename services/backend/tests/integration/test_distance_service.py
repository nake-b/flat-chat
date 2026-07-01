"""Integration tests for `DistanceService.resolve` — the distance-lens provider.

Executes against Postgres (PostGIS `ST_Distance`): a compile-only test would
miss a geography-cast / operator-shape regression, exactly the class of bug the
project's integration tier exists to catch. Mirrors the `near_place_ref` search
test — seed an EXTENDED geometry (a LINESTRING landmark, ~the Spree) and listings
at known offsets, then assert the returned metres are distance to the SHAPE (not
its centroid), matching the search filter's semantics.

Gated on `TEST_DATABASE_URL` (skipped when unset).
"""

from __future__ import annotations

import sqlalchemy as sa
from geoalchemy2 import WKTElement

from flat_chat.listings.context import Marker
from flat_chat.listings.lenses import DistanceLens
from flat_chat.search.distance import DistanceService

from ..conftest import DB_REQUIRED
from ..fixtures.factories import gold_row as _gold_row
from ..fixtures.factories import listing_row as _listing_row
from ..fixtures.factories import with_session as _with_session

pytestmark = DB_REQUIRED


def test_resolve_measures_distance_to_geometry(async_db_url):
    # A long E-W line at lat 52.50 (lon 13.30→13.50). `on_line` sits ~110 m from
    # the LINE (but ~6.5 km from its centroid); `elsewhere` sits ~11 km away. The
    # returned metres must be to the SHAPE, so on_line << elsewhere.
    on_line = _listing_row(
        latitude=52.501,
        longitude=13.305,
        location=WKTElement("POINT(13.305 52.501)", srid=4326),
    )
    elsewhere = _listing_row(
        latitude=52.40,
        longitude=13.30,
        location=WKTElement("POINT(13.30 52.40)", srid=4326),
    )
    seeds = [
        (on_line, _gold_row(on_line["id"])),
        (elsewhere, _gold_row(elsewhere["id"])),
    ]

    async def body(session):
        landmark_id = await session.scalar(
            sa.text(
                """
                INSERT INTO world.landmarks (name, source, category, geom)
                VALUES (
                    'Spree', 'osm', 'river',
                    ST_SetSRID(
                        ST_GeomFromText('LINESTRING(13.30 52.50, 13.50 52.50)'),
                        4326
                    )
                )
                RETURNING id
                """
            )
        )
        markers = [
            Marker(id=str(on_line["id"]), lat=52.501, lng=13.305),
            Marker(id=str(elsewhere["id"]), lat=52.40, lng=13.30),
        ]
        lens = DistanceLens(
            anchor_label="Spree",
            anchor_lat=52.50,
            anchor_lng=13.40,
            near_place_ref=f"landmark:{landmark_id}",
        )
        return await DistanceService(session).resolve(markers, lens)

    out = _with_session(async_db_url, seeds, body)
    on_id, else_id = str(on_line["id"]), str(elsewhere["id"])
    assert on_id in out and else_id in out
    # Distance to the LINE, not the centroid: on_line ~110 m, elsewhere ~11 km.
    assert out[on_id] < 300
    assert out[else_id] > 5000
    assert out[on_id] < out[else_id]


def test_resolve_malformed_ref_returns_empty(async_db_url):
    a = _listing_row(location=WKTElement("POINT(13.40 52.50)", srid=4326))
    seeds = [(a, _gold_row(a["id"]))]

    async def body(session):
        lens = DistanceLens(
            anchor_label="?",
            anchor_lat=52.5,
            anchor_lng=13.4,
            near_place_ref="not-a-real-token",  # no colon → parse None → {}
        )
        return await DistanceService(session).resolve(
            [Marker(id=str(a["id"]), lat=52.5, lng=13.4)], lens
        )

    assert _with_session(async_db_url, seeds, body) == {}
