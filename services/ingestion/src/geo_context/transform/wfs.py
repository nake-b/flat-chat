"""Transform a WFS GeoDataFrame into the silver-table shape.

Steps applied to every layer:
  1. Reproject geom to EPSG:4326 (silver-tier standard)
  2. Rename source columns to the English silver-column names via aliases
  3. Drop any unaliased columns (avoids accidental leakage of German names)
  4. Optionally inject extra fixed columns (e.g. `tier` for hospitals)
  5. Apply value-level translations (e.g. MSS German→English labels) so
     silver stores canonical English — the data layer is language-agnostic
     and downstream consumers (gold, search, frontend) never see German.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from .aliases import ALIASES

logger = logging.getLogger(__name__)

SILVER_SRID = 4326


# ---------------------------------------------------------------------------
# Value-level translations: German source labels → canonical English silver
# values. Threshold doc §8 owns the MSS label vocabulary; this table is the
# *single point* where the publisher's German strings stop existing.
#
# Why this lives here (not in the backend): silver is the canonical clean
# form. If the publisher renames a label, only this map changes and a
# geo_context.run refresh propagates English everywhere downstream — gold,
# search filters, agent prose, frontend chips. No code in
# `services/backend/` ever sees German.
# ---------------------------------------------------------------------------

_MSS_STATUS_DE_TO_EN: dict[str, str] = {
    "sehr niedrig": "disadvantaged",
    "niedrig": "lower-income",
    "mittel": "mixed",
    "hoch": "affluent",
}

_MSS_DYNAMICS_DE_TO_EN: dict[str, str] = {
    "positiv": "improving",
    "stabil": "stable",
    "negativ": "slipping",
}

# Social-inequality composite label translation. The source publishes a
# pattern like "Status mittel , Dynamik stabil" — we machine-translate
# the two known German tokens into English. The "Status / Dynamik" frame
# stays as-is because it's structural, not idiomatic.
def _translate_social_inequality(val: str) -> str:
    if not isinstance(val, str):
        return val
    out = val
    for de, en in (
        ("sehr niedrig", "disadvantaged"),
        ("niedrig", "lower-income"),
        ("mittel", "mixed"),
        ("hoch", "affluent"),
        ("positiv", "improving"),
        ("stabil", "stable"),
        ("negativ", "slipping"),
        ("Status", "Status"),
        ("Dynamik", "Dynamics"),
    ):
        out = out.replace(de, en)
    return out


_MSS_SOCIAL_INEQUALITY_TRANSLATE = _translate_social_inequality


# Per-dataset/layer translation specs. Keyed by (dataset, layer) — same
# shape as ALIASES so the lookup is consistent. Values are either a dict
# (lookup) or a callable (str → str transform).
_VALUE_TRANSLATIONS: dict[tuple[str, str], dict] = {
    ("mss_2025", "mss2025_indizes_542"): {
        "status_index_label": _MSS_STATUS_DE_TO_EN,
        "dynamics_index_label": _MSS_DYNAMICS_DE_TO_EN,
        "social_inequality_label": _MSS_SOCIAL_INEQUALITY_TRANSLATE,
    },
}


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

    # 4b. Value-level translations (e.g. MSS German labels → English).
    # Applied per dataset/layer; the spec is a no-op for everything except
    # the MSS planning-area indices. Spec values are either a `dict`
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
    for col in renamed.columns:
        if col == "geom":
            continue
        s = renamed[col]
        if pd.api.types.is_float_dtype(s):
            non_null = s.dropna()
            if not non_null.empty and (non_null % 1 == 0).all():
                renamed[col] = s.astype("Int64")

    return renamed
