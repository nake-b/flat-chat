"""Tests for the GTFS transform: station collapse, modes/lines aggregation,
and canonical route-shape picking."""

from __future__ import annotations

import pandas as pd
import pytest

from geo_context.transform.gtfs import (
    build_route_shapes,
    build_routes,
    build_stops,
    transform_gtfs,
)


def _stops() -> pd.DataFrame:
    # Alexanderplatz parent station + 2 platform children, plus 1 standalone bus stop.
    return pd.DataFrame(
        [
            {
                "stop_id": "alex_station",
                "stop_name": "Alexanderplatz",
                "stop_lat": 52.5219,
                "stop_lon": 13.4132,
                "location_type": 1,
                "parent_station": None,
            },
            {
                "stop_id": "alex_u2",
                "stop_name": "Alexanderplatz (U2)",
                "stop_lat": 52.5219,
                "stop_lon": 13.4132,
                "location_type": 0,
                "parent_station": "alex_station",
            },
            {
                "stop_id": "alex_u5",
                "stop_name": "Alexanderplatz (U5)",
                "stop_lat": 52.5219,
                "stop_lon": 13.4132,
                "location_type": 0,
                "parent_station": "alex_station",
            },
            {
                "stop_id": "bus_42",
                "stop_name": "Random Bus Stop",
                "stop_lat": 52.5,
                "stop_lon": 13.4,
                "location_type": 0,
                "parent_station": None,
            },
        ]
    )


def _routes() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "route_id": "U2",
                "route_short_name": "U2",
                "route_long_name": "Pankow – Ruhleben",
                "route_type": 1,
                "route_color": "FF0000",
                "route_text_color": "FFFFFF",
            },
            {
                "route_id": "U5",
                "route_short_name": "U5",
                "route_long_name": "Hönow – Hauptbahnhof",
                "route_type": 1,
                "route_color": "AABBCC",
                "route_text_color": "000000",
            },
            {
                "route_id": "bus100",
                "route_short_name": "100",
                "route_long_name": "Zoo – Alexanderplatz",
                "route_type": 3,
                "route_color": None,
                "route_text_color": None,
            },
        ]
    )


def _trips() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trip_id": "t1", "route_id": "U2", "direction_id": 0, "shape_id": "s1"},
            {"trip_id": "t2", "route_id": "U2", "direction_id": 0, "shape_id": "s1"},
            {"trip_id": "t3", "route_id": "U2", "direction_id": 0, "shape_id": "s2"},
            {"trip_id": "t4", "route_id": "U5", "direction_id": 1, "shape_id": "s3"},
            {
                "trip_id": "t5",
                "route_id": "bus100",
                "direction_id": 0,
                "shape_id": None,
            },
        ]
    )


def _stop_times() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trip_id": "t1", "stop_id": "alex_u2"},
            {"trip_id": "t2", "stop_id": "alex_u2"},
            {"trip_id": "t3", "stop_id": "alex_u2"},
            {"trip_id": "t4", "stop_id": "alex_u5"},
            {"trip_id": "t5", "stop_id": "bus_42"},
        ]
    )


def test_stops_collapse_to_parent_station() -> None:
    out = build_stops(_stops(), _stop_times(), _trips(), _routes())
    ids = set(out["stop_id"])
    # parent station + standalone bus stop only; the two platforms are folded.
    assert ids == {"alex_station", "bus_42"}


def test_stops_modes_and_lines_aggregated() -> None:
    out = build_stops(_stops(), _stop_times(), _trips(), _routes())
    alex = out[out["stop_id"] == "alex_station"].iloc[0]
    # both U2 and U5 are subway (route_type 1) — modes_served = [1]
    assert alex["modes_served"] == [1]
    assert set(alex["lines_served"]) == {"U2", "U5"}


def test_routes_color_gets_hash_prefix() -> None:
    out = build_routes(_routes())
    assert out[out["route_id"] == "U2"].iloc[0]["color"] == "#FF0000"
    # null color stays null
    assert pd.isna(out[out["route_id"] == "bus100"].iloc[0]["color"])


def test_route_shapes_picks_most_used_per_direction() -> None:
    shapes = pd.DataFrame(
        [
            # s1 is a 2-point line, used by 2 trips (winner for U2 dir 0)
            {
                "shape_id": "s1",
                "shape_pt_sequence": 1,
                "shape_pt_lat": 52.5,
                "shape_pt_lon": 13.4,
            },
            {
                "shape_id": "s1",
                "shape_pt_sequence": 2,
                "shape_pt_lat": 52.6,
                "shape_pt_lon": 13.5,
            },
            # s2 is a different shape, used by 1 trip
            {
                "shape_id": "s2",
                "shape_pt_sequence": 1,
                "shape_pt_lat": 52.5,
                "shape_pt_lon": 13.4,
            },
            {
                "shape_id": "s2",
                "shape_pt_sequence": 2,
                "shape_pt_lat": 52.7,
                "shape_pt_lon": 13.6,
            },
            # s3 is U5 direction 1
            {
                "shape_id": "s3",
                "shape_pt_sequence": 1,
                "shape_pt_lat": 52.5,
                "shape_pt_lon": 13.4,
            },
            {
                "shape_id": "s3",
                "shape_pt_sequence": 2,
                "shape_pt_lat": 52.4,
                "shape_pt_lon": 13.3,
            },
        ]
    )
    out = build_route_shapes(shapes, _trips())
    keys = {(r["route_id"], r["direction_id"]) for _, r in out.iterrows()}
    # U2/0 (winner: s1) and U5/1 (only shape: s3).
    # bus100's trip has no shape_id → no row.
    assert keys == {("U2", 0), ("U5", 1)}


def test_transform_gtfs_returns_all_three_outputs() -> None:
    tables = {
        "stops": _stops(),
        "routes": _routes(),
        "trips": _trips(),
        "stop_times": _stop_times(),
        "shapes": pd.DataFrame(
            [
                {
                    "shape_id": "s1",
                    "shape_pt_sequence": 1,
                    "shape_pt_lat": 52.5,
                    "shape_pt_lon": 13.4,
                },
                {
                    "shape_id": "s1",
                    "shape_pt_sequence": 2,
                    "shape_pt_lat": 52.6,
                    "shape_pt_lon": 13.5,
                },
            ]
        ),
    }
    out = transform_gtfs(tables)
    assert set(out.keys()) == {
        "transit_stops",
        "transit_routes",
        "transit_route_shapes",
    }
    assert len(out["transit_stops"]) > 0
    assert len(out["transit_routes"]) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
