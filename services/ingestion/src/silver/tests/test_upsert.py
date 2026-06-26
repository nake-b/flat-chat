"""Regression tests for the silver UPSERT coordinate-preservation rule.

The bug this guards: a re-transform of a coordinate-less source (e.g.
wohninberlin, whose points are geocoded out-of-band) used to NULL the
latitude/longitude columns on conflict. Because the map card is built from
those columns, that silently evicted the listing from search on the next
`silver.run`. The fix: never clobber a stored coordinate with NULL.
"""

from silver.upsert import COORD_COLS, conflict_update_set


def test_drops_conflict_key():
    out = conflict_update_set(
        {"source_name": "x", "external_id": "y", "title": "t",
         "latitude": 52.5, "longitude": 13.4}
    )
    assert "source_name" not in out
    assert "external_id" not in out
    assert out["title"] == "t"


def test_preserves_existing_point_when_incoming_has_none():
    # No coordinates on the incoming row -> coord columns must NOT be in the
    # update set, so ON CONFLICT leaves the stored point untouched.
    out = conflict_update_set(
        {"source_name": "wohninberlin", "external_id": "42",
         "title": "t", "latitude": None, "longitude": None}
    )
    for col in COORD_COLS:
        assert col not in out, f"{col} would clobber a stored coordinate with NULL"
    assert out["title"] == "t"  # non-coord columns still refresh


def test_partial_coords_treated_as_missing():
    # latitude present but longitude None (or vice versa) is not a valid point.
    out = conflict_update_set(
        {"source_name": "s", "external_id": "1", "latitude": 52.5, "longitude": None}
    )
    for col in COORD_COLS:
        assert col not in out


def test_overwrites_point_when_incoming_has_coords():
    # A row that DOES carry a point still refreshes the stored coordinates.
    out = conflict_update_set(
        {"source_name": "s", "external_id": "1",
         "latitude": 52.5, "longitude": 13.4, "location": "POINT(...)"}
    )
    assert out["latitude"] == 52.5
    assert out["longitude"] == 13.4
    assert out["location"] == "POINT(...)"
