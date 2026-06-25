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

    dtype_map: dict[str, Any] = {
        "geom": Geometry(pg_type, srid=SILVER_SRID)
    }

    if extra_dtype:
        dtype_map.update(extra_dtype)

    # 1) DROPPING PHASE: Drop any rows containing corrupted out-of-bounds numbers
    PG_INT_MIN = -2147483648
    PG_INT_MAX = 2147483647

    for col in gdf.columns:
        is_integer_field = (
            col in ["planting_year", "age_years"] or 
            (extra_dtype and col in extra_dtype and isinstance(extra_dtype[col], sa.Integer))
        )
        
        if is_integer_field:
            v_numeric = pd.to_numeric(gdf[col], errors='coerce')
            corrupted_rows = (v_numeric < PG_INT_MIN) | (v_numeric > PG_INT_MAX)
            
            if corrupted_rows.any():
                logger.warning(
                    f"Table '{table_name}': Dropped {corrupted_rows.sum()} rows "
                    f"due to corrupted out-of-range values in column '{col}'"
                )
                gdf = gdf[~corrupted_rows].copy()

    def safe_int(s: pd.Series) -> pd.Series:
        # Explicit list comprehension guarantees absolute execution control over item types.
        # This keeps integers as pure python ints and converts missing keys to native Nones.
        numeric = pd.to_numeric(s, errors="coerce")
        return pd.Series(
            [int(round(x)) if pd.notna(x) else None for x in numeric],
            index=s.index,
            dtype=object
        )

    def is_int_like(series: pd.Series) -> bool:
        """
        Detects columns like: 28.0, 15.0, NaN, "28"
        but NOT true floats like 28.5
        """
        numeric = pd.to_numeric(series, errors="coerce")
        return (numeric.dropna() % 1 == 0).all()

    # 2) FIX: Handle planting years safely
    if 'planting_year' in gdf.columns:
        years = pd.to_numeric(gdf['planting_year'], errors='coerce')
        gdf['planting_year'] = pd.Series(
            [int(round(x)) if (pd.notna(x) and 1000 <= x <= 2100) else None for x in years],
            index=gdf.index,
            dtype=object
        )

    if extra_dtype:
        # Explicit schema-based cast
        for col, col_dtype in extra_dtype.items():
            if col not in gdf.columns:
                continue
            if isinstance(col_dtype, sa.Integer):
                gdf[col] = safe_int(gdf[col])

    # 3) HARD SAFETY PASS: catch missed float-integers and nullable Int64 types
    for col in gdf.columns:
        if col == 'planting_year':
            continue  # Already safely processed above
            
        if col in dtype_map and isinstance(dtype_map[col], sa.Integer):
            gdf[col] = safe_int(gdf[col])
        elif gdf[col].dtype == "float64" and is_int_like(gdf[col]):
            gdf[col] = safe_int(gdf[col])
        elif str(gdf[col].dtype) == "Int64":
            gdf[col] = safe_int(gdf[col])

    # 4) Debug inspection assert
    if "age_years" in gdf.columns:
        print("\n========== BEFORE TO_POSTGIS ==========")
        print("dtype:", gdf["age_years"].dtype)
        non_nulls = gdf["age_years"].dropna()
        print("python type:", type(non_nulls.iloc[0]) if not non_nulls.empty else "All None/Empty")
        print(gdf["age_years"].head(20).tolist())
        print("=======================================\n")

    # 5) Push clean data safely to database
    gdf.to_postgis(
        table_name,
        conn,
        if_exists="append",
        index=False,
        chunksize=chunksize,
        dtype=dtype_map,
    )


def to_pg_array(values: Any, *, kind: str) -> str | None:
    """Serialize a Python list into a Postgres array literal string."""
    if values is None:
        return None
    if isinstance(values, float) and pd.isna(values):
        return None
    if len(values) == 0:
        return "{}"
    if kind == "int":
        return "{" + ",".join(str(int(v)) for v in values) + "}"
    if kind == "text":
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