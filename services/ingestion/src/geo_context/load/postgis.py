"""PostGIS loaders for geo_context.

Two layers of API:

- ``_write_replace`` / ``_write_append`` / ``_write_dataframe`` take an
  existing ``sa.Connection`` and do not open their own transaction. Use
  these when multiple loads must commit together (e.g. hospitals_plan +
  hospitals_other → ``hospitals``, or the GTFS triplet) so a mid-family
  failure rolls the whole family back.
- ``load_replace`` / ``load_append`` / ``load_dataframe`` are thin wrappers
  that open ``engine.begin()`` for a single-table call. Single-table
  callers keep the simple ``engine`` signature.
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
    # Target the ingestion-owned `world` schema explicitly. The engine pins
    # search_path=world,public (db.py), but geopandas' to_postgis defaults its
    # existence check + Find_SRID to the literal `public` schema — so post
    # schema-split (tables live in `world`) the SRID lookup fails without this.
    gdf.to_postgis(
        table_name,
        conn,
        schema="world",
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


def _write_replace(
    conn: sa.Connection,
    gdf: gpd.GeoDataFrame,
    table_name: str,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Truncate + append inside an EXISTING transaction. Caller owns commit."""
    if gdf.empty:
        logger.warning("write_replace %s: empty GeoDataFrame, skipping", table_name)
        return 0
    _truncate(conn, table_name)
    _write_gdf(
        gdf,
        table_name,
        conn,
        geom_type=geom_type,
        chunksize=chunksize,
        extra_dtype=extra_dtype,
    )
    logger.info("write_replace %s: wrote %d rows", table_name, len(gdf))
    return len(gdf)


def _write_append(
    conn: sa.Connection,
    gdf: gpd.GeoDataFrame,
    table_name: str,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Append rows inside an EXISTING transaction. Caller owns commit."""
    if gdf.empty:
        logger.warning("write_append %s: empty GeoDataFrame, skipping", table_name)
        return 0
    _write_gdf(
        gdf,
        table_name,
        conn,
        geom_type=geom_type,
        chunksize=chunksize,
        extra_dtype=extra_dtype,
    )
    logger.info("write_append %s: appended %d rows", table_name, len(gdf))
    return len(gdf)


def _write_dataframe(
    conn: sa.Connection,
    df: pd.DataFrame,
    table_name: str,
    *,
    chunksize: int = 5000,
) -> int:
    """Truncate + append for a non-spatial DataFrame inside an EXISTING transaction."""
    if df.empty:
        logger.warning("write_dataframe %s: empty DataFrame, skipping", table_name)
        return 0
    _truncate(conn, table_name)
    df.to_sql(
        table_name,
        conn,
        if_exists="append",
        index=False,
        chunksize=chunksize,
        method="multi",
    )
    logger.info("write_dataframe %s: wrote %d rows", table_name, len(df))
    return len(df)


def load_replace(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Single-table TRUNCATE + INSERT in one transaction (convenience wrapper)."""
    with engine.begin() as conn:
        return _write_replace(
            conn,
            gdf,
            table_name,
            geom_type=geom_type,
            chunksize=chunksize,
            extra_dtype=extra_dtype,
        )


def load_append(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    geom_type: str = "Point",
    chunksize: int = 5000,
    extra_dtype: dict[str, Any] | None = None,
) -> int:
    """Single-table append in one transaction (convenience wrapper)."""
    with engine.begin() as conn:
        return _write_append(
            conn,
            gdf,
            table_name,
            geom_type=geom_type,
            chunksize=chunksize,
            extra_dtype=extra_dtype,
        )


def load_dataframe(
    df: pd.DataFrame,
    table_name: str,
    engine: sa.Engine,
    *,
    chunksize: int = 5000,
) -> int:
    """Single-table TRUNCATE + INSERT for a non-spatial DataFrame (wrapper)."""
    with engine.begin() as conn:
        return _write_dataframe(conn, df, table_name, chunksize=chunksize)
