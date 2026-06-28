"""Transform a WFS GeoDataFrame into the silver-table shape.

Steps applied to every layer:
  1. Reproject geom to EPSG:4326 (silver-tier standard)
  2. Rename source columns to the English silver-column names via aliases
  3. Drop any unaliased columns (avoids accidental leakage of German names)
  4. Optionally inject extra fixed columns (e.g. `tier` for hospitals)
  5. Optionally drop rows with an empty `name`, then rows whose name is a
     generic building-function label (named-only layers, e.g. the ALKIS
     building footprints that seed `landmarks` — an unnamed or generically-
     named footprint is just a building, not a landmark).
"""

from __future__ import annotations

import logging
import re

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from .aliases import ALIASES

logger = logging.getLogger(__name__)

SILVER_SRID = 4326


# ---------------------------------------------------------------------------
# Named-only layers: drop rows whose `name` column is null/blank after the
# rename. ALKIS publishes every building footprint, but only NAMED ones are
# landmarks (Fernsehturm, Siegessäule, …); an unnamed footprint is generic
# building noise we don't want in the `landmarks` table or the named_places
# search view.
# ---------------------------------------------------------------------------

_NAMED_ONLY_LAYERS: set[tuple[str, str]] = {
    ("alkis_gebaeude", "alkis_gebaeude:gebaeude"),
}

# ---------------------------------------------------------------------------
# Generic-name stoplist (named-only layers). ALKIS names thousands of building
# footprints after their FUNCTION rather than a proper noun — 530× "Kinderta-
# gesstätte", 238× "Sporthalle", 100× "Turnhalle", … (5833 ALKIS rows collapse
# to ~3541 distinct names). These are useless as gazetteer entries (nobody
# searches "near a Sporthalle") and flood `locate_place` trigram results; the
# dedicated `kita`/`school`/`hospital` gazetteer kinds already cover the intent.
#
# We drop rows whose name (case-insensitively, stripped) EXACTLY matches an
# entry here — exact match, so specific names that merely CONTAIN a generic
# word survive ("Phorms Schule Berlin Mitte", "St.-Marien-Kirche", a named
# "Kindertagesstätte Sonnenschein"). Proper-noun institutions that happen to
# repeat (Deutscher Bundestag, Technische Universität Berlin, Robert-Koch-
# Institut) are intentionally NOT listed — they're legitimate places.
#
# NOTE: this removes generic-FUNCTION noise; it does not de-duplicate the
# remaining proper-noun multi-polygons (e.g. 9× "Technische Universität
# Berlin"). Collapsing those to one representative geometry is a separate
# follow-up — it needs a post-load pass, not a per-page transform filter.
_GENERIC_LANDMARK_NAMES: frozenset[str] = frozenset(
    {
        # childcare / youth
        "kindertagesstätte",
        "kita",
        "hort",
        "jugendfreizeitheim",
        "jugendfreizeiteinrichtung",
        "jugendfreizeitstätte",
        "jugendclub",
        "jugendzentrum",
        "jugendverkehrsschule",
        "jugendgesundheitsdienst",
        # sport halls
        "sporthalle",
        "turnhalle",
        "schwimmhalle",
        "reithalle",
        "tennishalle",
        # schools / education (generic; named schools survive)
        "schule",
        "berufsschule",
        "privatschule",
        "musikschule",
        "mensa",
        "bibliothek",
        # worship (generic; named churches survive)
        "kapelle",
        "kirche",
        "dorfkirche",
        "friedenskirche",
        "christuskirche",
        "herz-jesu-kirche",
        "neuapostolische kirche",
        "moschee",
        "gemeindehaus",
        "vereinshaus",
        # care / social
        "seniorenheim",
        "seniorenwohnhaus",
        "seniorenwohnhäuser",
        "seniorenfreizeitstätte",
        "wohnheim",
        "sozialstation",
        "ärztehaus",
        "gesundheitsamt",
        # infrastructure / utilities
        "rettungsstation",
        "wasserrettungsstation",
        "pumpwerk",
        "pumpstation",
        "heizwerk",
        "umspannwerk",
        "tiefgarage",
        # public services
        "feuerwehr",
        "freiw. feuerwehr",
        "freiwillige feuerwehr",
        "polizei",
        "bezirksamt",
        "bürgeramt",
        "finanzamt",
        "jobcenter",
        "heimatmuseum",
        # transit-stop / address fragments
        "u-bf.",
        "s-bf.",
        "siedlung",
        # affiliation fragments ("[building belonging] to the X")
        "zur humboldt-universität zu berlin",
        "zur charité",
    }
)

# "Haus 7", "Haus 30" — bare building-number labels within a campus (Charité,
# ministries). Pure noise as place names; caught by pattern, not enumeration.
_GENERIC_LANDMARK_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"^haus\s+\d+$"),)


def _is_generic_landmark_name(name: str) -> bool:
    """True if `name` is a generic building-function label, not a real place."""
    norm = name.strip().lower()
    if norm in _GENERIC_LANDMARK_NAMES:
        return True
    return any(p.match(norm) for p in _GENERIC_LANDMARK_PATTERNS)


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
    projected = (
        gdf.to_crs(epsg=SILVER_SRID) if gdf.crs.to_epsg() != SILVER_SRID else gdf
    )

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
                    lambda g: (
                        make_valid(g)
                        if isinstance(g, BaseGeometry) and not g.is_valid
                        else g
                    )
                )
            }
        )

    # 2. Rename + 3. drop unaliased columns.
    # Keep the geometry column always; drop everything else not in rename_map.
    geom_col = projected.geometry.name
    keep_cols = [c for c in projected.columns if c in rename_map or c == geom_col]
    dropped = [c for c in projected.columns if c not in keep_cols]
    if dropped:
        logger.debug(
            "%s/%s: dropping %d unaliased columns: %s",
            dataset,
            layer,
            len(dropped),
            dropped,
        )

    renamed = projected[keep_cols].rename(columns=rename_map)

    # geopandas' rename can detach the active geometry — re-set explicitly.
    if geom_col != "geom":
        renamed = renamed.rename_geometry("geom")

    # 4. Inject constants (e.g. discriminator tier for the hospitals union,
    # source/category for the landmarks union).
    if extra_columns:
        for col, value in extra_columns.items():
            renamed[col] = value

    # 4b. Named-only filter: drop rows with a null/blank `name`, then drop rows
    # whose name is a generic building-function label (see _GENERIC_LANDMARK_*).
    # Only the landmark-seeding layers opt in (see _NAMED_ONLY_LAYERS) — an
    # unnamed or generically-named ALKIS footprint is noise, not a landmark.
    if key in _NAMED_ONLY_LAYERS and "name" in renamed.columns:
        before = len(renamed)
        name_str = renamed["name"].astype("string").str.strip()
        renamed = renamed[name_str.notna() & (name_str != "")]
        dropped_unnamed = before - len(renamed)
        if dropped_unnamed:
            logger.info(
                "%s/%s: dropped %d unnamed rows (named-only layer)",
                dataset,
                layer,
                dropped_unnamed,
            )

        before_generic = len(renamed)
        generic_mask = renamed["name"].map(_is_generic_landmark_name)
        renamed = renamed[~generic_mask]
        dropped_generic = before_generic - len(renamed)
        if dropped_generic:
            logger.info(
                "%s/%s: dropped %d generic-name rows (stoplist)",
                dataset,
                layer,
                dropped_generic,
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
