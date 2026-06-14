"""PostGIS loaders for geo_context.

Two modes:
- `load_replace`: TRUNCATE + INSERT in one transaction. Used for tables
  fed by a single source (most of them).
- `load_append`: INSERT without truncate. Used when multiple WFS layers
  feed the same table (hospitals: plan_hospital + other).

Both wrap the write in `engine.begin()` so a mid-write failure rolls back
and leaves the previous data intact.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import geopandas as gpd
import pandas as pd
import sqlalchemy as sa
from geoalchemy2 import Geometry

logger = logging.getLogger(__name__)

SILVER_SRID = 4326

# Lowercase snake_case identifiers only — matches every silver table created
# by migrations 0003/0004. Validating before interpolating into TRUNCATE
# makes the f-string provably injection-safe even if the caller ever starts
# passing dynamic names. Postgres won't bind identifiers via parameters,
# so an allowlist regex is the standard mitigation.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _SAFE_IDENT.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


_GEOM_KIND_BY_TYPE: dict[str, str] = {
    "Point": "POINT",
    "MultiPoint": "MULTIPOINT",
    "LineString": "LINESTRING",
    "MultiLineString": "MULTILINESTRING",
    "Polygon": "POLYGON",
    "MultiPolygon": "MULTIPOLYGON",
    # Generic — for tables whose source publishes mixed types (e.g. water_bodies
    # mixes Polygon / MultiPolygon / GeometryCollection).
    "Geometry": "GEOMETRY",
}


def _truncate(conn: sa.Connection, table_name: str) -> None:
    safe = _safe_ident(table_name)
    conn.execute(sa.text(f'TRUNCATE TABLE "{safe}" RESTART IDENTITY CASCADE'))


def _write_gdf(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    conn: sa.Connection,
    *,
    geom_type: str,
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> None:
    pg_type = _GEOM_KIND_BY_TYPE.get(geom_type)
    if pg_type is None:
        raise ValueError(f"unsupported geom_type: {geom_type!r}")
    dtype: dict[str, Any] = {"geom": Geometry(pg_type, srid=SILVER_SRID)}
    if extra_dtype:
        dtype.update(extra_dtype)
    gdf.to_postgis(
        table_name,
        conn,
        if_exists="append",
        index=False,
        chunksize=chunksize,
        dtype=dtype,
    )


def to_pg_array(values: Any, *, kind: str) -> str | None:
    """Serialize a Python list into a Postgres array literal string.

    Geopandas's to_postgis writes via COPY, which can't translate Python
    lists into Postgres array literals. Pre-serializing the column to a
    text-formatted array literal (\"{1,2}\" / \"{a,b}\") lets COPY write
    the text verbatim, and Postgres' input coercion parses it into a
    real array on insert.

    `kind`: \"int\" for numeric arrays, \"text\" for string arrays.
    """
    if values is None:
        return None
    if isinstance(values, float) and pd.isna(values):
        return None
    if len(values) == 0:
        return "{}"
    if kind == "int":
        return "{" + ",".join(str(int(v)) for v in values) + "}"
    if kind == "text":
        # Quote and escape backslashes + double-quotes per Postgres array rules.
        escaped = [
            '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
            for v in values
        ]
        return "{" + ",".join(escaped) + "}"
    raise ValueError(f"unsupported kind: {kind!r}")


def load_replace(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Truncate the target table, then append rows in one transaction."""
    if gdf.empty:
        logger.warning("load_replace %s: empty GeoDataFrame, skipping", table_name)
        return 0
    with engine.begin() as conn:
        _truncate(conn, table_name)
        _write_gdf(
            gdf,
            table_name,
            conn,
            geom_type=geom_type,
            chunksize=chunksize,
            extra_dtype=extra_dtype,
        )
    logger.info("load_replace %s: wrote %d rows", table_name, len(gdf))
    return len(gdf)


def load_append(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Append rows without touching existing data."""
    if gdf.empty:
        logger.warning("load_append %s: empty GeoDataFrame, skipping", table_name)
        return 0
    with engine.begin() as conn:
        _write_gdf(
            gdf,
            table_name,
            conn,
            geom_type=geom_type,
            chunksize=chunksize,
            extra_dtype=extra_dtype,
        )
    logger.info("load_append %s: appended %d rows", table_name, len(gdf))
    return len(gdf)


def load_dataframe(
    df: pd.DataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    chunksize: int = 5000,
) -> int:
    """Truncate + append for a plain (non-spatial) DataFrame, e.g. transit_routes."""
    if df.empty:
        logger.warning("load_dataframe %s: empty DataFrame, skipping", table_name)
        return 0
    with engine.begin() as conn:
        _truncate(conn, table_name)
        df.to_sql(
            table_name,
            conn,
            if_exists="append",
            index=False,
            chunksize=chunksize,
            method="multi",
        )
    logger.info("load_dataframe %s: wrote %d rows", table_name, len(df))
    return len(df)
