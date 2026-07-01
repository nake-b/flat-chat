"""Turn a GTFS feed into transit_stops / transit_routes / transit_route_shapes.

Three transforms:

1. `build_stops`  — collapses GTFS platform children onto their parent
                    station, computes modes_served + lines_served + wheelchair
                    summary. One row per logical stop or station.

2. `build_routes` — straight column projection from routes.txt.

3. `build_route_shapes`
                  — picks one canonical shape per (route_id, direction_id) by
                    counting trips and converts the shape's point sequence
                    into a single LineString.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

logger = logging.getLogger(__name__)

SILVER_SRID = 4326


def build_stops(
    stops: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    routes: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Collapse platforms to parents, attach modes_served + lines_served.

    Returns one GeoDataFrame row per logical stop or station. Platform children
    are folded into their parent: by `parent_station` when the feed sets it, and
    otherwise by the base DHID (the part of the stop_id before the `::` quay
    separator) — VBB leaves `parent_station` empty on its `::N` quays, so the
    DHID fallback is what actually reunites them. Each complex's modes and lines
    are unioned across its members.
    """
    # GTFS allows location_type to be NaN — treat as 0 (regular stop).
    if "location_type" not in stops.columns:
        stops = stops.assign(location_type=0)
    stops = stops.copy()
    stops["location_type"] = stops["location_type"].fillna(0).astype(int)

    # Discard non-boardable nodes: type 2 (entrance/exit), 3 (generic node),
    # 4 (boarding area). VBB uses 2/3 heavily and they'd otherwise win the
    # location_type-DESC drop_duplicates and force the canonical row to a
    # non-boardable point. We only want type 0 (stop/platform) and 1 (station).
    stops = stops[stops["location_type"].isin([0, 1])]

    if "parent_station" not in stops.columns:
        stops["parent_station"] = pd.NA

    # 1. Effective ID: the parent if there is one, else the stop's base DHID.
    #    VBB stop_ids are DHIDs — a parent station `de:11000:900009102` plus
    #    platform quays `de:11000:900009102::5`, `::6`, ... — but VBB does NOT
    #    populate parent_station on the quays, so a `parent_station`-only collapse
    #    leaves each quay as its own row, each with a partial lines_served (~48%
    #    of stops ended up duplicated; "U Leopoldplatz" appeared 7×). So when
    #    there's no explicit parent we fall back to the base DHID — everything
    #    before the `::` quay separator — to reunite the quays. We split ONLY on
    #    `::` (a single `:` is the DHID's own field separator); a plain id with
    #    no `::` (test fixtures, non-DHID feeds) passes through unchanged. An
    #    explicit parent_station is trusted as-is and never stripped.
    base_dhid = stops["stop_id"].str.split("::", n=1).str[0]
    effective_id = stops["parent_station"].where(
        stops["parent_station"].notna() & (stops["parent_station"] != ""),
        base_dhid,
    )
    stops = stops.assign(effective_id=effective_id)

    # 2. Each row of stop_times → its stop's effective_id.
    st = stop_times[["trip_id", "stop_id"]].merge(
        stops[["stop_id", "effective_id"]], on="stop_id", how="left"
    )
    # 3. trip → route, with the route_type and short_name we need.
    st = st.merge(trips[["trip_id", "route_id"]], on="trip_id", how="left")
    st = st.merge(
        routes[["route_id", "route_type", "route_short_name"]],
        on="route_id",
        how="left",
    )

    # 4. Aggregate per effective_id.
    grouped = st.dropna(subset=["effective_id"]).groupby("effective_id")
    modes_served = (
        grouped["route_type"]
        .apply(lambda s: sorted({int(v) for v in s.dropna()}))
        .rename("modes_served")
    )
    lines_served = (
        grouped["route_short_name"]
        .apply(lambda s: sorted({str(v).strip() for v in s.dropna() if str(v).strip()}))
        .rename("lines_served")
    )
    aggs = pd.concat([modes_served, lines_served], axis=1).reset_index()

    # 5. Pick the canonical row per effective_id by priority:
    #      0) a real station node (location_type==1)
    #      1) the bare base row (stop_id == effective_id) — its coordinate is the
    #         station point
    #      2) any quay — coordinate proxy (quays of one DHID sit within ~50 m,
    #         well inside overlay precision)
    #    A `== stop_id` filter would drop whole complexes that have ONLY quay
    #    rows and no base/station row (VBB has these), so we rank instead.
    priority = pd.Series(2, index=stops.index)
    priority = priority.mask(stops["stop_id"] == stops["effective_id"], 1)
    priority = priority.mask(stops["location_type"] == 1, 0)
    canonical = (
        stops.assign(_priority=priority)
        .sort_values(by=["effective_id", "_priority"], kind="stable")
        .drop_duplicates(subset="effective_id", keep="first")
    )

    # 6. Merge aggregates on the shared effective_id (single key → no
    #    effective_id_x / effective_id_y collision). Stops with no stop_times
    #    rows get dropped (inner join).
    out = canonical.merge(aggs, on="effective_id", how="inner")

    # 7. Build geometry + final column set. The emitted stop_id is the
    #    effective_id (the collapsed identity) so quays don't re-fragment
    #    downstream — every consumer keys on the one row per complex.
    geom = [
        Point(float(lon), float(lat))
        for lat, lon in zip(out["stop_lat"], out["stop_lon"], strict=True)
    ]
    gdf = gpd.GeoDataFrame(
        {
            "stop_id": out["effective_id"].astype(str),
            "name": out["stop_name"].astype(str),
            "geom": geom,
            "modes_served": out["modes_served"],
            "lines_served": out["lines_served"],
        },
        geometry="geom",
        crs=f"EPSG:{SILVER_SRID}",
    )
    logger.info("gtfs stops: %d effective stations/stops", len(gdf))
    return gdf


def build_routes(routes: pd.DataFrame) -> pd.DataFrame:
    """routes.txt → transit_routes column shape."""
    cols = {
        "route_id": routes["route_id"].astype(str),
        "short_name": routes.get("route_short_name"),
        "long_name": routes.get("route_long_name"),
        "route_type": routes["route_type"].astype(int),
        "color": _maybe_prefix_hash(routes.get("route_color")),
        "text_color": _maybe_prefix_hash(routes.get("route_text_color")),
    }
    out = pd.DataFrame(cols)
    logger.info("gtfs routes: %d rows", len(out))
    return out


def _maybe_prefix_hash(s: pd.Series | None) -> pd.Series | None:
    """GTFS publishes route_color without the leading '#'. Add it."""
    if s is None:
        return None
    return s.apply(
        lambda v: f"#{v}" if pd.notna(v) and not str(v).startswith("#") else v
    )


def build_route_shapes(shapes: pd.DataFrame, trips: pd.DataFrame) -> gpd.GeoDataFrame:
    """Pick one canonical shape per (route_id, direction_id) and convert
    its point sequence into a single LineString."""
    if shapes.empty:
        return gpd.GeoDataFrame(
            columns=["route_id", "direction_id", "geom"],
            geometry="geom",
            crs=f"EPSG:{SILVER_SRID}",
        )

    # direction_id is optional in GTFS — default to 0.
    if "direction_id" not in trips.columns:
        trips = trips.assign(direction_id=0)
    trips = trips.copy()
    trips["direction_id"] = trips["direction_id"].fillna(0).astype(int)

    # Count trips per (route, direction, shape) and pick the winner per pair.
    counts = (
        trips.dropna(subset=["shape_id"])
        .groupby(["route_id", "direction_id", "shape_id"])
        .size()
        .reset_index(name="trip_count")
    )
    winners = counts.sort_values(
        ["route_id", "direction_id", "trip_count"], ascending=[True, True, False]
    ).drop_duplicates(subset=["route_id", "direction_id"], keep="first")

    # Build a LineString per winning shape_id, then join back.
    shapes_sorted = shapes.sort_values(["shape_id", "shape_pt_sequence"])
    lines = (
        shapes_sorted.groupby("shape_id")
        .apply(
            lambda g: LineString(zip(g["shape_pt_lon"], g["shape_pt_lat"], strict=True))
        )
        .rename("geom")
        .reset_index()
    )

    merged = winners.merge(lines, on="shape_id", how="inner")
    gdf = gpd.GeoDataFrame(
        {
            "route_id": merged["route_id"].astype(str),
            "direction_id": merged["direction_id"].astype(int),
            "geom": merged["geom"],
        },
        geometry="geom",
        crs=f"EPSG:{SILVER_SRID}",
    )
    logger.info("gtfs route_shapes: %d (route,direction) pairs", len(gdf))
    return gdf


def transform_gtfs(
    tables: dict[str, pd.DataFrame],
) -> dict[str, gpd.GeoDataFrame | pd.DataFrame]:
    """Run all three transforms and return the per-table outputs."""
    return {
        "transit_stops": build_stops(
            tables["stops"], tables["stop_times"], tables["trips"], tables["routes"]
        ),
        "transit_routes": build_routes(tables["routes"]),
        "transit_route_shapes": build_route_shapes(tables["shapes"], tables["trips"]),
    }
