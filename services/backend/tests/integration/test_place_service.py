"""Integration tests for `PlaceService.locate` (the `locate_place` backend).

`PlaceService` reads the ingestion-owned `world.named_places` VIEW and
resolves a free-text place name to candidate `place_ref` tokens via the
pg_trgm `%` operator + `similarity()` ranking. These tests EXECUTE against
Postgres — the `%` operator and the GIN trigram indexes exist only there
(Phase 1's 0007 migration), so a compile-only test would miss an operator
or index regression.

Each test seeds a row in a base table behind the view (`landmarks`,
`parks`), runs `locate`, and asserts the candidate comes back with the
view-composed `place_ref` and a centroid lat/lon. Rolled back per test.
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flat_chat.search.places import PlaceService

from ..conftest import DB_REQUIRED

pytestmark = DB_REQUIRED


def _run(async_url: str, body):
    async def _wrapped():
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                trans = await conn.begin()
                try:
                    session = AsyncSession(bind=conn, expire_on_commit=False)
                    try:
                        return await body(session)
                    finally:
                        await session.close()
                finally:
                    await trans.rollback()
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


def test_locate_resolves_by_trigram_similarity(async_db_url):
    """A seeded landmark is found by a fuzzy / partial name; the view
    composes `place_ref` as `landmark:<id>` and lat/lon are the centroid."""

    async def body(session: AsyncSession):
        landmark_id = await session.scalar(
            sa.text(
                """
                INSERT INTO world.landmarks (name, source, category, geom)
                VALUES (
                    'Brandenburger Tor', 'osm', 'monument',
                    ST_SetSRID(ST_MakePoint(13.3777, 52.5163), 4326)
                )
                RETURNING id
                """
            )
        )
        # Fuzzy / partial query — trigram match, not exact equality.
        candidates = await PlaceService(session).locate("Brandenburg Tor")
        return landmark_id, candidates

    landmark_id, candidates = _run(async_db_url, body)
    assert candidates, "expected at least one trigram match"
    top = candidates[0]
    assert top.place_ref == f"landmark:{landmark_id}"
    assert top.kind == "landmark"
    assert top.name == "Brandenburger Tor"
    # Centroid of a point is the point itself.
    assert top.lat is not None and abs(top.lat - 52.5163) < 1e-3
    assert top.lon is not None and abs(top.lon - 13.3777) < 1e-3


def test_locate_returns_centroid_for_extended_geometry(async_db_url):
    """A LINESTRING (e.g. the Spree) still yields a single display point via
    ST_Centroid, and resolves through the `landmark:` view branch."""

    async def body(session: AsyncSession):
        await session.execute(
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
                """
            )
        )
        return await PlaceService(session).locate("Spree")

    candidates = _run(async_db_url, body)
    assert candidates
    top = candidates[0]
    assert top.name == "Spree"
    # Centroid of the E-W line sits at ~lon 13.40, lat 52.50.
    assert top.lat is not None and abs(top.lat - 52.50) < 1e-3
    assert top.lon is not None and abs(top.lon - 13.40) < 1e-2


def test_locate_empty_query_returns_empty(async_db_url):
    async def body(session: AsyncSession):
        return await PlaceService(session).locate("   ")

    assert _run(async_db_url, body) == []


def test_locate_no_match_returns_empty(async_db_url):
    async def body(session: AsyncSession):
        return await PlaceService(session).locate("zzzzx-no-such-place-qqqq")

    assert _run(async_db_url, body) == []


# ---------------------------------------------------------------------------
# overlay_geometry — resolve a place_ref to drawable GeoJSON (the map-overlay
# read path). Executes against PostGIS: ST_AsGeoJSON + ST_SimplifyPreserveTopology
# only exist there, and the kind+src_id prune through the named_places view is
# the same shape SearchService uses for ST_DWithin.
# ---------------------------------------------------------------------------


def test_overlay_geometry_returns_simplified_geojson(async_db_url):
    """A seeded LINESTRING landmark (the Spree) resolves to a MapOverlay whose
    geojson is a GeoJSON LineString; kind='place', label=the name."""

    async def body(session: AsyncSession):
        landmark_id = await session.scalar(
            sa.text(
                "INSERT INTO world.landmarks (name, source, category, geom) "
                "VALUES ('Spree', 'osm', 'river', "
                "ST_SetSRID(ST_GeomFromText(:wkt), 4326)) RETURNING id"
            ),
            {"wkt": "LINESTRING(13.30 52.50, 13.40 52.51, 13.50 52.50)"},
        )
        overlay = await PlaceService(session).overlay_geometry(
            f"landmark:{landmark_id}", origin="pinned"
        )
        return overlay

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.kind == "place"
    assert overlay.label == "Spree"
    assert overlay.origin == "pinned"
    assert overlay.id == overlay.id  # stable id round-trips
    assert overlay.geojson["type"] == "LineString"
    assert len(overlay.geojson["coordinates"]) >= 2


def test_overlay_geometry_defaults_to_search_origin(async_db_url):
    async def body(session: AsyncSession):
        poly = "POLYGON((13.33 52.51, 13.36 52.51, 13.35 52.52, 13.33 52.51))"
        landmark_id = await session.scalar(
            sa.text(
                "INSERT INTO world.landmarks (name, source, category, geom) "
                "VALUES ('Tiergarten', 'osm', 'park', "
                "ST_SetSRID(ST_GeomFromText(:wkt), 4326)) RETURNING id"
            ),
            {"wkt": poly},
        )
        return await PlaceService(session).overlay_geometry(f"landmark:{landmark_id}")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.origin == "search"
    assert overlay.geojson["type"] == "Polygon"


def test_overlay_geometry_unknown_ref_returns_none(async_db_url):
    async def body(session: AsyncSession):
        svc = PlaceService(session)
        garbage = await svc.overlay_geometry("not-a-real-ref")
        missing = await svc.overlay_geometry("landmark:999999999")
        return garbage, missing

    garbage, missing = _run(async_db_url, body)
    assert garbage is None  # malformed token fails closed
    assert missing is None  # valid format, nonexistent id


def _box(lon: float, lat: float, d: float = 0.001) -> str:
    """A small square POLYGON WKT anchored at (lon, lat)."""
    return (
        f"POLYGON(({lon} {lat}, {lon + d} {lat}, {lon + d} {lat + d}, "
        f"{lon} {lat + d}, {lon} {lat}))"
    )


async def _insert_landmark(session, name, wkt, *, category="building", source="alkis"):
    return await session.scalar(
        sa.text(
            "INSERT INTO world.landmarks (name, source, category, geom) "
            "VALUES (:n, :s, :c, ST_SetSRID(ST_GeomFromText(:wkt), 4326)) RETURNING id"
        ),
        {"n": name, "s": source, "c": category, "wkt": wkt},
    )


def test_locate_prefers_polygon_over_coincident_point(async_db_url):
    """A seed-alias POINT and a building POLYGON share a name; the polygon must
    rank first (the ST_Dimension tiebreak), so overlays draw a shape not a dot."""

    async def body(session: AsyncSession):
        point_id = await _insert_landmark(
            session,
            "Glühwurmplatz",
            "POINT(13.40 52.50)",
            category="alias",
            source="seed",
        )
        poly_id = await _insert_landmark(
            session,
            "Glühwurmplatz",
            _box(13.40, 52.50),
        )
        return point_id, poly_id, await PlaceService(session).locate("Glühwurmplatz")

    point_id, poly_id, candidates = _run(async_db_url, body)
    assert candidates
    # Equal trigram score (identical name) → polygon wins the tiebreak.
    assert candidates[0].place_ref == f"landmark:{poly_id}"
    assert f"landmark:{point_id}" in {c.place_ref for c in candidates}


def test_overlay_geometry_unions_local_same_named_cluster(async_db_url):
    """Same-named footprints within the cluster radius union into one shape; a
    distant same-named one and a nearby differently-named one are excluded."""

    async def body(session: AsyncSession):
        near_a = await _insert_landmark(
            session,
            "Campus Q",
            _box(13.40, 52.50),
        )
        # ~150 m east, same name → unioned.
        await _insert_landmark(
            session,
            "Campus Q",
            _box(13.402, 52.50),
        )
        # ~7 km away, same name → excluded (different local cluster).
        await _insert_landmark(
            session,
            "Campus Q",
            _box(13.50, 52.55),
        )
        # Adjacent but differently named → excluded (no fuzzy swallow).
        await _insert_landmark(
            session,
            "Nachbarhaus",
            _box(13.4005, 52.5005, 0.0001),
        )
        return await PlaceService(session).overlay_geometry(f"landmark:{near_a}")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.label == "Campus Q"
    # Exactly the two near same-named footprints → a 2-part MultiPolygon.
    assert overlay.geojson["type"] == "MultiPolygon"
    assert len(overlay.geojson["coordinates"]) == 2


def test_overlay_geometry_drops_coincident_point_keeps_polygon(async_db_url):
    """A polygon footprint and a coincident same-named alias POINT must union to
    a clean Polygon (richest dimension only) — never a GeometryCollection."""

    async def body(session: AsyncSession):
        poly_id = await _insert_landmark(session, "Mixed Campus", _box(13.41, 52.49))
        await _insert_landmark(
            session,
            "Mixed Campus",
            "POINT(13.4105 52.4905)",
            category="alias",
            source="seed",
        )
        return await PlaceService(session).overlay_geometry(f"landmark:{poly_id}")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.geojson["type"] == "Polygon"  # the point was dropped


def test_overlay_geometry_alias_point_snaps_to_footprint(async_db_url):
    """A seed-alias POINT ("TU Berlin") whose own name has no polygon snaps to
    the footprint it sits on (a differently-named building 20 m away) and draws
    THAT — not a dot — while the chip keeps the alias name."""

    async def body(session: AsyncSession):
        # The real building the pin marks — a different name than the alias.
        await _insert_landmark(session, "Hauptgebäude der TU", _box(13.40, 52.50))
        # The seed alias point sitting ~20 m from that building.
        alias_id = await _insert_landmark(
            session,
            "TU Muster",
            "POINT(13.4005 52.5002)",
            category="alias",
            source="seed",
        )
        return await PlaceService(session).overlay_geometry(f"landmark:{alias_id}")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.label == "TU Muster"  # chip keeps the alias the user named
    # Snapped to the building footprint — a polygon, not the alias point.
    assert overlay.geojson["type"] == "Polygon"


# ---------------------------------------------------------------------------
# Rich candidate menu (issue #38) + curated university priority. Each seeds
# rows behind the view and asserts the ranking / annotation `locate` now emits.
# ---------------------------------------------------------------------------


async def _insert_park(session, name, wkt):
    return await session.scalar(
        sa.text(
            "INSERT INTO world.parks (name, geom) "
            "VALUES (:n, ST_SetSRID(ST_GeomFromText(:wkt), 4326)) RETURNING id"
        ),
        {"n": name, "wkt": wkt},
    )


async def _insert_curated(
    session, name, wkt, *, kind="university", description=None, anchor=None
):
    """anchor: optional (lon, lat) main-building point stored as float columns."""
    return await session.scalar(
        sa.text(
            "INSERT INTO world.curated_places "
            "(kind, name, description, geom, anchor_lon, anchor_lat) "
            "VALUES (:k, :n, :d, ST_SetSRID(ST_GeomFromText(:wkt), 4326), "
            ":alon, :alat) RETURNING id"
        ),
        {
            "k": kind,
            "n": name,
            "d": description,
            "wkt": wkt,
            "alon": anchor[0] if anchor else None,
            "alat": anchor[1] if anchor else None,
        },
    )


async def _insert_ortsteil(session, name, wkt):
    await session.execute(
        sa.text(
            "INSERT INTO world.ortsteile (name, geom) "
            "VALUES (:n, ST_SetSRID(ST_GeomFromText(:wkt), 4326))"
        ),
        {"n": name, "wkt": wkt},
    )


def test_locate_prefers_area_over_higher_scoring_point(async_db_url):
    """Issue #38: a park POLYGON must rank above a coincident POINT that scores
    HIGHER on raw similarity (the bare name), because the score bucket + the
    ST_Dimension tiebreak promote the searchable area — not only at exact ties.
    Also asserts `geom_kind` is populated (area vs point)."""

    async def body(session: AsyncSession):
        # Bare-named point → similarity 1.0 (would win under the old ORDER BY).
        await _insert_landmark(
            session,
            "Wieselheide",
            "POINT(13.42 52.48)",
            category="alias",
            source="seed",
        )
        # Park polygon whose longer name scores LOWER on plain similarity but is
        # a word-match — the real "Volkspark Hasenheide vs the bus stop" shape.
        park_id = await _insert_park(
            session, "Volkspark Wieselheide-Grünanlage", _box(13.42, 52.48, 0.004)
        )
        return park_id, await PlaceService(session).locate("Wieselheide")

    park_id, candidates = _run(async_db_url, body)
    assert candidates
    top = candidates[0]
    assert top.place_ref == f"park:{park_id}"
    assert top.kind == "park"
    assert top.geom_kind == "area"
    # The coincident point is still offered (rich menu), just not first.
    assert any(c.geom_kind == "point" for c in candidates)


def test_locate_populates_locality_from_ortsteil(async_db_url):
    """A candidate's `locality` is the containing Ortsteil (point-in-polygon)."""

    async def body(session: AsyncSession):
        await _insert_ortsteil(
            session, "Wieselkiez", _box(13.40, 52.50, 0.05)
        )  # big enough to cover the landmark
        await _insert_landmark(session, "Dachsplatz", "POINT(13.41 52.51)")
        return await PlaceService(session).locate("Dachsplatz")

    candidates = _run(async_db_url, body)
    assert candidates
    assert candidates[0].locality == "Wieselkiez"
    assert candidates[0].geom_kind == "point"


def test_locate_curated_priority_outranks_footprint(async_db_url):
    """A curated_places row (priority 1) outranks a same-named raw landmark
    footprint (priority 0) even though both match identically."""

    async def body(session: AsyncSession):
        await _insert_landmark(session, "Zeta Universität", _box(13.30, 52.50))
        curated_id = await _insert_curated(
            session, "Zeta Universität – Campus Test", _box(13.30, 52.50, 0.003)
        )
        return curated_id, await PlaceService(session).locate("Zeta Universität")

    curated_id, candidates = _run(async_db_url, body)
    assert candidates
    top = candidates[0]
    assert top.place_ref == f"university:{curated_id}"
    assert top.kind == "university"


def test_locate_abbreviation_matches_via_word_similarity(async_db_url):
    """A short abbreviation ("ZQ") whose plain trigram similarity to the long
    curated name is BELOW the 0.3 `%` threshold still resolves — the `%>`
    word-similarity widening admits it (the whole point of the HU/TU fix)."""

    async def body(session: AsyncSession):
        name = "ZQ Berlin – Zeta-Quux-Universität – Campus Test"
        # Guard: prove plain similarity would have dropped it.
        sim = await session.scalar(
            sa.text("SELECT similarity('ZQ', :n)"), {"n": name}
        )
        curated_id = await _insert_curated(session, name, _box(13.31, 52.51, 0.003))
        return sim, curated_id, await PlaceService(session).locate("ZQ")

    sim, curated_id, candidates = _run(async_db_url, body)
    assert sim < 0.3, "test premise: bare abbreviation is below the % threshold"
    assert any(c.place_ref == f"university:{curated_id}" for c in candidates)


def test_locate_returns_multiple_distinct_campuses(async_db_url):
    """When a name resolves to several distinct curated campuses (the HU case),
    all are returned so the agent can ask which — none is silently collapsed."""

    async def body(session: AsyncSession):
        base = "ZW Berlin – Zeta-Wobble-Universität – Campus "
        coords = [(13.30, 52.50), (13.40, 52.53), (13.52, 52.43)]
        ids = []
        for i, (lon, lat) in enumerate(coords):
            ids.append(
                await _insert_curated(session, f"{base}{i}", _box(lon, lat, 0.002))
            )
        return ids, await PlaceService(session).locate("ZW")

    ids, candidates = _run(async_db_url, body)
    refs = {c.place_ref for c in candidates}
    for cid in ids:
        assert f"university:{cid}" in refs, "every distinct campus must be offered"


# ---------------------------------------------------------------------------
# anchor_point — the single point the travel-time / distance lens routes to.
# Prefers the curated main-building anchor; else the footprint centroid.
# ---------------------------------------------------------------------------


def test_anchor_point_uses_main_building_when_set(async_db_url):
    """A curated campus with an `anchor` routes there — not the centroid of its
    (wide-spread) footprint."""

    async def body(session: AsyncSession):
        # Two far-apart building blocks → centroid sits ~midway (~13.31).
        wide = (
            "MULTIPOLYGON("
            f"({_box(13.30, 52.50).removeprefix('POLYGON(').removesuffix(')')}),"
            f"({_box(13.32, 52.50).removeprefix('POLYGON(').removesuffix(')')}))"
        )
        # Anchor pinned to the WEST block corner, far from the centroid.
        cid = await _insert_curated(
            session, "Zeta Uni – Campus Wide", wide, anchor=(13.30, 52.50)
        )
        anchor = await PlaceService(session).anchor_point(f"university:{cid}")
        return anchor

    anchor = _run(async_db_url, body)
    assert anchor is not None
    # Routes to the pinned main building (13.30), not the ~13.31 centroid.
    assert abs(anchor.lon - 13.30) < 1e-4
    assert abs(anchor.lat - 52.50) < 1e-3


def test_anchor_point_falls_back_to_centroid_without_anchor(async_db_url):
    """No curated anchor → the footprint centroid, as before."""

    async def body(session: AsyncSession):
        landmark_id = await _insert_landmark(
            session, "Zeta Solo Building", _box(13.40, 52.50, 0.002)
        )
        anchor = await PlaceService(session).anchor_point(f"landmark:{landmark_id}")
        return anchor

    anchor = _run(async_db_url, body)
    assert anchor is not None
    # Centroid of the ~200 m box ≈ its middle.
    assert abs(anchor.lon - 13.401) < 5e-3
    assert abs(anchor.lat - 52.501) < 5e-3


def test_overlay_geometry_alias_point_no_footprint_nearby_stays_point(async_db_url):
    """An isolated alias point with nothing solid within snap range draws itself
    (a Point) rather than nothing."""

    async def body(session: AsyncSession):
        alias_id = await _insert_landmark(
            session,
            "Lonely Alias",
            "POINT(13.10 52.40)",  # far from any seeded footprint
            category="alias",
            source="seed",
        )
        return await PlaceService(session).overlay_geometry(f"landmark:{alias_id}")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.geojson["type"] == "Point"


# ---------------------------------------------------------------------------
# B′ — transit stops in the gazetteer (0008). `world.transit_stops` is now a
# UNION arm of `world.named_places`, so locate_place / anchor_point /
# overlay_geometry resolve arbitrary S/U-Bahn/tram/bus stations. The arm
# de-dups per-platform rows by `GROUP BY name` (centroid), and `src_id` is text
# (a colon-laden GTFS stop_id). These EXECUTE against Postgres — the view, the
# trgm index, and the GROUP BY centroid only exist there.
# ---------------------------------------------------------------------------


async def _insert_transit_stop(session, stop_id, name, lon, lat):
    await session.execute(
        sa.text(
            "INSERT INTO world.transit_stops "
            "(stop_id, name, geom, modes_served, lines_served) VALUES "
            "(:sid, :n, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), "
            "ARRAY[1]::smallint[], ARRAY['U2']::text[])"
        ),
        {"sid": stop_id, "n": name, "lon": lon, "lat": lat},
    )


def test_locate_resolves_transit_stop_by_trigram(async_db_url):
    """A seeded station is found by a fuzzy name; the view composes a
    `transit_stop:<stop_id>` place_ref over the (text) GTFS stop_id."""

    async def body(session: AsyncSession):
        await _insert_transit_stop(
            session, "de:11000:900100003", "S+U Alexanderplatz", 13.4132, 52.5219
        )
        return await PlaceService(session).locate("Alexanderplatz")

    candidates = _run(async_db_url, body)
    assert candidates, "expected a trigram match on the station name"
    hit = next((c for c in candidates if c.kind == "transit_stop"), None)
    assert hit is not None
    assert hit.place_ref == "transit_stop:de:11000:900100003"
    assert hit.lat is not None and abs(hit.lat - 52.5219) < 1e-3
    assert hit.lon is not None and abs(hit.lon - 13.4132) < 1e-3


def test_locate_dedups_same_named_platforms_to_one_station(async_db_url):
    """VBB ships one row per platform; the `GROUP BY name` arm collapses them
    into ONE candidate at the centroid of the platforms."""

    async def body(session: AsyncSession):
        name = "Schönhauser Allee"
        await _insert_transit_stop(session, "stop:u2", name, 13.412, 52.549)
        await _insert_transit_stop(session, "stop:s41", name, 13.414, 52.549)
        return await PlaceService(session).locate(name)

    candidates = _run(async_db_url, body)
    stations = [c for c in candidates if c.kind == "transit_stop"]
    assert len(stations) == 1, "platforms must collapse to one station"
    # Centroid sits between the two platforms (~lon 13.413).
    assert stations[0].lat is not None and abs(stations[0].lat - 52.549) < 1e-3
    assert stations[0].lon is not None and abs(stations[0].lon - 13.413) < 1e-2


def test_anchor_point_resolves_transit_stop(async_db_url):
    """`apply_travel_time` reads `anchor_point` for the routing destination —
    a station ref must resolve to (name, lat, lon)."""

    async def body(session: AsyncSession):
        await _insert_transit_stop(session, "stop:ost", "Ostkreuz", 13.469, 52.503)
        return await PlaceService(session).anchor_point("transit_stop:stop:ost")

    anchor = _run(async_db_url, body)
    assert anchor is not None
    label, lat, lon = anchor
    assert label == "Ostkreuz"
    assert abs(lat - 52.503) < 1e-3
    assert abs(lon - 13.469) < 1e-3


def test_overlay_geometry_transit_stop_stays_point_not_snapped(async_db_url):
    """A station overlay must draw the station POINT — NOT snap to a nearby
    building footprint the way a seed-alias point does. Guards the kind-guard:
    we seed a polygon landmark right next to the stop and assert the overlay is
    still the Point."""

    async def body(session: AsyncSession):
        # A building footprint 20 m from the stop — the snap path would grab it.
        await _insert_landmark(session, "Bahnhofsgebäude", _box(13.469, 52.503))
        await _insert_transit_stop(session, "stop:ost2", "Ostkreuz", 13.4695, 52.5032)
        return await PlaceService(session).overlay_geometry("transit_stop:stop:ost2")

    overlay = _run(async_db_url, body)
    assert overlay is not None
    assert overlay.kind == "place"
    assert overlay.label == "Ostkreuz"
    assert overlay.geojson["type"] == "Point"  # not snapped to the polygon
