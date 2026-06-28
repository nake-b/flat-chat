"""Curated landmark seed loader.

Reads ``landmark_seed.yaml`` — hand-verified iconic landmarks, informal areas,
and abbreviation aliases that OSM/ALKIS miss or tag inconsistently — into a
``landmarks``-shaped GeoDataFrame (name, description, category, geom in
EPSG:4326). ``geo_context.run`` loads it with ``source='seed'``.

See agent-compound-docs/decisions/named-place-search.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import yaml
from shapely import wkt
from shapely.errors import ShapelyError

logger = logging.getLogger(__name__)

# landmark_seed.yaml sits at the geo_context package root (one level up).
SEED_PATH = Path(__file__).resolve().parent.parent / "landmark_seed.yaml"
SILVER_SRID = 4326


def load_seed_frame(path: Path | None = None) -> gpd.GeoDataFrame:
    """Parse the seed YAML into a (name, description, category, geom) frame.

    A row missing name/category/geometry, or carrying unparseable/empty WKT,
    is skipped with a warning — a single bad row never aborts the load.
    """
    src = path or SEED_PATH
    raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    rows = raw.get("seeds") or []

    names: list[str] = []
    descriptions: list[str | None] = []
    categories: list[str] = []
    geoms: list = []
    for i, row in enumerate(rows):
        name = (row.get("name") or "").strip()
        category = (row.get("category") or "").strip()
        wkt_str = (row.get("geometry") or "").strip()
        if not (name and category and wkt_str):
            logger.warning("seed[%d]: missing name/category/geometry — skipped", i)
            continue
        try:
            geom = wkt.loads(wkt_str)
        except ShapelyError, ValueError, TypeError:
            logger.warning("seed[%d] %r: unparseable WKT — skipped", i, name)
            continue
        if geom.is_empty:
            logger.warning("seed[%d] %r: empty geometry — skipped", i, name)
            continue
        names.append(name)
        descriptions.append(row.get("description"))
        categories.append(category)
        geoms.append(geom)

    gdf = gpd.GeoDataFrame(
        {"name": names, "description": descriptions, "category": categories},
        geometry=geoms,
        crs=f"EPSG:{SILVER_SRID}",
    )
    # The PostGIS loader writes the active geometry column; `landmarks.geom`.
    return gdf.rename_geometry("geom")
