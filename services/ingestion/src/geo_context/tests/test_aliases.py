"""Smoke tests for the German→English column rename map.

For each (dataset, layer) in ALIASES we build a minimal one-row GeoDataFrame
with the German column names and assert that transform_wfs_layer:
  - emits every expected English column,
  - drops any German source column,
  - projects geom to EPSG:4326.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from geo_context.transform.aliases import ALIASES
from geo_context.transform.wfs import transform_wfs_layer


@pytest.mark.parametrize("key", list(ALIASES.keys()))
def test_alias_roundtrip(key: tuple[str, str]) -> None:
    dataset, layer = key
    rename_map = ALIASES[key]
    # one-row GeoDataFrame in EPSG:25833 with placeholder German columns.
    data = {src: f"sample_{i}" for i, src in enumerate(rename_map)}
    data["geometry"] = [Point(1000.0, 2000.0)]
    gdf = gpd.GeoDataFrame(data, crs="EPSG:25833")

    out = transform_wfs_layer(gdf, dataset, layer)

    # every English column must appear
    for english in rename_map.values():
        assert english in out.columns, f"missing english col {english!r} for {key}"
    # no German source column may leak through
    for german in rename_map:
        if german not in rename_map.values():
            assert german not in out.columns, f"leaked german col {german!r} for {key}"
    # geometry was projected to 4326
    assert out.crs is not None and out.crs.to_epsg() == 4326


def test_extra_columns_injected() -> None:
    """When a YAML entry specifies `extra: {tier: plan_hospital}` the
    orchestrator passes it through to transform_wfs_layer."""
    dataset, layer = ("krankenhaeuser", "plankrankenhaeuser")
    rename_map = ALIASES[(dataset, layer)]
    data = {src: "x" for src in rename_map}
    data["geometry"] = [Point(0, 0)]
    gdf = gpd.GeoDataFrame(data, crs="EPSG:25833")

    out = transform_wfs_layer(
        gdf, dataset, layer, extra_columns={"tier": "plan_hospital"}
    )
    assert "tier" in out.columns
    assert out["tier"].iloc[0] == "plan_hospital"


def test_unknown_dataset_raises() -> None:
    gdf = gpd.GeoDataFrame(
        {"geometry": [Point(0, 0)]}, crs="EPSG:25833"
    )
    with pytest.raises(KeyError):
        transform_wfs_layer(gdf, "nope", "nope")
