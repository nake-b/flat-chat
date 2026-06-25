"""Transform a WFS GeoDataFrame into the silver-table shape.

Steps applied to every layer:
  1. Reproject geom to EPSG:4326 (silver-tier standard)
  2. Rename source columns to the English silver-column names via aliases
  3. Drop any unaliased columns (avoids accidental leakage of German names)
  4. Optionally inject extra fixed columns (e.g. `tier` for hospitals)
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from .aliases import ALIASES

logger = logging.getLogger(__name__)

SILVER_SRID = 4326

_FORCE_INT_COLUMNS = {
    "num_storeys",
    "planting_year",
    "age_years",
}
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


_VALUE_TRANSLATIONS: dict[tuple[str, str], dict] = {}


def transform_wfs_layer(
    gdf: gpd.GeoDataFrame,
    dataset: str,
    layer: str,
    *,
    extra_columns: dict[str, object] | None = None,
) -> gpd.GeoDataFrame:
    """Project + rename + filter to silver columns.

    Args:
        gdf: Raw WFS output, geom in source CRS.
        dataset, layer: Lookup key into ALIASES.
        extra_columns: Constant columns to inject (e.g. {"tier": "plan_hospital"}).

    Returns:
        New GeoDataFrame in EPSG:4326 with only the silver-table columns + geom.
    """
    key = (dataset, layer)
    if key not in ALIASES:
        raise KeyError(f"no ALIASES entry for {key!r} — add one before loading")
    rename_map = ALIASES[key]

    # 1. Project — silver is always EPSG:4326 for cross-table joinability.
    if gdf.crs is None:
        raise ValueError(f"{dataset}/{layer}: GeoDataFrame has no CRS set")
    projected = gdf.to_crs(epsg=SILVER_SRID) if gdf.crs.to_epsg() != SILVER_SRID else gdf

    # Repair self-intersecting / invalid polygons via shapely make_valid.
    # No-op for geometries that are already valid. PostGIS would otherwise
    # accept them silently and break later ST_Contains / ST_Intersects calls.
    # Points / MultiPoints can't be invalid (a Point is just an (x,y)), so
    # skip the Python-level apply entirely for those layers — saves ~3.8M
    # function calls on the noise raster.
    geom_types = set(projected.geometry.geom_type.unique())
    if not geom_types.issubset({"Point", "MultiPoint"}):
        projected = projected.assign(
            **{
                projected.geometry.name: projected.geometry.apply(
                    lambda g: make_valid(g) if isinstance(g, BaseGeometry) and not g.is_valid else g
                )
            }
        )

    # 2. Rename + 3. drop unaliased columns.
    # Keep the geometry column always; drop everything else not in rename_map.
    geom_col = projected.geometry.name
    keep_cols = [c for c in projected.columns if c in rename_map or c == geom_col]
    dropped = [c for c in projected.columns if c not in keep_cols]
    if dropped:
        logger.debug("%s/%s: dropping %d unaliased columns: %s",
                     dataset, layer, len(dropped), dropped)

    renamed = projected[keep_cols].rename(columns=rename_map)

    # geopandas' rename can detach the active geometry — re-set explicitly.
    if geom_col != "geom":
        renamed = renamed.rename_geometry("geom")

    # 4. Inject constants (e.g. discriminator tier for the hospitals union).
    if extra_columns:
        for col, value in extra_columns.items():
            renamed[col] = value

    if key == ("kita", "kita"):
        if "address" not in renamed.columns:
            renamed["address"] = None
        if "street" in renamed.columns:
            street = renamed["street"].fillna("").astype(str).str.strip()
            house = (
                renamed["house_number"].fillna("").astype(str).str.strip()
                if "house_number" in renamed.columns
                else ""
            )
            composed = (street + " " + house).str.strip()
            renamed["address"] = renamed["address"].where(
                renamed["address"].notna(),
                composed.replace("", pd.NA),
            )

    if key == ("alkis_gebaeude", "gebaeude") and "name" in renamed.columns:
        names = renamed["name"].astype("string")
        keep_mask = names.notna() & names.str.strip().ne("")
        dropped = int((~keep_mask).sum())
        if dropped > 0:
            logger.info(
                "%s/%s: dropping %d unnamed building features",
                dataset,
                layer,
                dropped,
            )
        renamed = renamed.loc[keep_mask].copy()

    for col in _FORCE_INT_COLUMNS:
        if col in renamed.columns:
            numeric = pd.to_numeric(renamed[col], errors="coerce")
            numeric = numeric.where(np.isfinite(numeric), np.nan)
            rounded = np.round(numeric)
            in_range = rounded.between(_INT64_MIN, _INT64_MAX, inclusive="both")
            overflow_count = int((rounded.notna() & ~in_range).sum())
            if overflow_count > 0:
                logger.warning(
                    "%s/%s: column %s has %d out-of-range values for Int64; coercing to NULL",
                    dataset,
                    layer,
                    col,
                    overflow_count,
                )
            rounded = rounded.where(in_range, np.nan)
            renamed[col] = pd.array(rounded, dtype="Int64")

    # 4b. Value-level translations for selected dataset/layer pairs.
    # Spec values are either a `dict`
    # (lookup table — unmapped values fall through unchanged and are
    # logged) or a `callable` (custom transform — applied to every
    # non-null cell).
    translation_spec = _VALUE_TRANSLATIONS.get(key)
    if translation_spec:
        for column, mapping in translation_spec.items():
            if column not in renamed.columns:
                continue
            if callable(mapping):
                renamed[column] = renamed[column].map(
                    lambda v, fn=mapping: fn(v) if v is not None else v
                )
            else:
                unmapped = set(renamed[column].dropna().unique()) - set(mapping.keys())
                if unmapped:
                    logger.warning(
                        "%s/%s: column %s has unmapped values %s — kept as-is",
                        dataset, layer, column, sorted(unmapped),
                    )
                renamed[column] = renamed[column].map(
                    lambda v, m=mapping: m.get(v, v)
                )

    # 5. Coerce whole-number float columns to nullable Int64. pandas turns
    # any integer source column containing nulls into float64 (1, 2, NaN
    # → 1.0, 2.0, NaN); Postgres COPY then refuses "1.0" for an INTEGER
    # column. If every non-null value is a whole number, treat the column
    # as integer.

    # 5. Coerce whole-number float columns to nullable Int64. pandas turns
    # any integer source column containing nulls into float64 (1, 2, NaN
    # → 1.0, 2.0, NaN); Postgres COPY then refuses "1.0" for an INTEGER
    # column. If every non-null value is a whole number, treat the column
    # as integer.

    for col in renamed.columns:
        if col == "geom":
            continue

        try:
            s = renamed[col]

            # only numeric columns
            if not pd.api.types.is_numeric_dtype(s):
                continue

            s = pd.to_numeric(s, errors="coerce")

            # fix float noise
            s = np.round(s)

            # ONLY cast if safe
            if (s.dropna() % 1 == 0).all():
                # Vektorisierter, sicherer Cast für Float64 mit NaNs zu Int64
                mask = s.isna()
                filled = s.fillna(0).astype("int64")
                casted = pd.Series(filled, dtype="Int64", index=s.index)
                casted[mask] = pd.NA
                renamed[col] = casted
            else:
                renamed[col] = s

        except Exception:
            logger.exception("failed column %s", col)
            

    return renamed
