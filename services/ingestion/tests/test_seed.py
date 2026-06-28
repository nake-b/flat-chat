"""Unit tests for the curated landmark seed parser (no DB)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from geo_context.extract.seed import load_seed_frame


def test_load_seed_frame_keeps_valid_skips_bad(tmp_path: Path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text(
        textwrap.dedent(
            """
            seeds:
              - name: East Side Gallery
                category: attraction
                geometry: "LINESTRING(13.43 52.50, 13.44 52.50)"
              - name: TU Berlin
                category: alias
                geometry: "POINT(13.3269 52.5125)"
              - name: BrokenWkt
                category: area
                geometry: "NOT WKT AT ALL"
              - name: ""
                category: area
                geometry: "POINT(13.4 52.5)"
              - name: MissingGeom
                category: area
            """
        ),
        encoding="utf-8",
    )

    gdf = load_seed_frame(p)

    # Only the two well-formed rows survive; bad WKT / blank name / missing
    # geometry are skipped (not fatal).
    assert set(gdf["name"]) == {"East Side Gallery", "TU Berlin"}
    # Loader contract: geometry column is `geom`, SRID 4326.
    assert gdf.geometry.name == "geom"
    assert gdf.crs is not None and gdf.crs.to_epsg() == 4326


def test_real_seed_file_parses() -> None:
    """The shipped landmark_seed.yaml is valid and non-empty."""
    gdf = load_seed_frame()
    assert not gdf.empty
    assert {"name", "category", "geom"}.issubset(gdf.columns)
    # Alias + area rows exist and stay search-only (non-notable categories).
    assert {"alias", "area"} & set(gdf["category"])
