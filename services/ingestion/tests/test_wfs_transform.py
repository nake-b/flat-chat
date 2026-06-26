"""Unit tests for the WFS transform — named-only filter + extra-column inject.

No DB: builds a small GeoDataFrame and asserts the transform's column rename,
constant injection, and the named-only row drop (used by the ALKIS landmark
seed layer). Mirrors the hospitals `tier`-injection contract for the new
`landmarks` source/category injection.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point

from geo_context.transform.wfs import transform_wfs_layer

_ALKIS_KEY = ("alkis_gebaeude", "alkis_gebaeude:gebaeudebauwerk")


def _alkis_frame() -> gpd.GeoDataFrame:
    # `nam` → name, `bezeich` → description per the aliases; one row unnamed.
    return gpd.GeoDataFrame(
        {
            "nam": ["Fernsehturm", "", None],
            "bezeich": ["Tower", "Generic", "Generic"],
            "junk_col": ["x", "y", "z"],
            "geometry": [Point(13.4, 52.5), Point(13.41, 52.5), Point(13.42, 52.5)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def test_named_only_layer_drops_unnamed_rows() -> None:
    out = transform_wfs_layer(
        _alkis_frame(),
        *_ALKIS_KEY,
        extra_columns={"source": "alkis", "category": "building"},
    )
    # Only the row with a non-empty name survives.
    assert len(out) == 1
    assert out.iloc[0]["name"] == "Fernsehturm"


def test_named_only_layer_injects_source_and_category() -> None:
    out = transform_wfs_layer(
        _alkis_frame(),
        *_ALKIS_KEY,
        extra_columns={"source": "alkis", "category": "building"},
    )
    assert out.iloc[0]["source"] == "alkis"
    assert out.iloc[0]["category"] == "building"
    assert out.iloc[0]["description"] == "Tower"


def test_unaliased_columns_are_dropped() -> None:
    out = transform_wfs_layer(
        _alkis_frame(),
        *_ALKIS_KEY,
        extra_columns={"source": "alkis", "category": "building"},
    )
    assert "junk_col" not in out.columns
    assert "nam" not in out.columns  # renamed to `name`
