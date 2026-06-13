"""CLI orchestrator for the geo-context ETL.

Usage:
    python -m geo_context.run               # run every enabled dataset
    python -m geo_context.run --only schools,parks
    python -m geo_context.run --skip-gtfs   # WFS only

Each (dataset, layer) is wrapped in its own try/except — a single layer
failing does not abort the run. Exit code 1 if any layer failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from db import engine

from .config import Catalog, WfsDataset, load_catalog
from .extract.gtfs import VbbGtfsClient
from .extract.wfs import BerlinGdiWfsClient
from .load.postgis import load_append, load_dataframe, load_replace, to_pg_array
from .transform.gtfs import transform_gtfs
from .transform.wfs import transform_wfs_layer

logger = logging.getLogger(__name__)


def _run_wfs(
    catalog: Catalog,
    only: set[str] | None,
    wfs_client: BerlinGdiWfsClient,
) -> tuple[int, int]:
    """Returns (ok_count, fail_count)."""
    ok, fail = 0, 0

    # Group WFS datasets by target table so we know when multiple layers
    # share one table (e.g. hospitals_plan + hospitals_other → hospitals).
    by_table: dict[str, list[WfsDataset]] = {}
    for ds in catalog.wfs:
        if not ds.enabled:
            continue
        if only and ds.key not in only:
            continue
        by_table.setdefault(ds.table, []).append(ds)

    for table, entries in by_table.items():
        # First entry truncates; subsequent entries append. Done across the
        # whole table so multi-source tables (hospitals) start fresh.
        first = True
        for ds in entries:
            try:
                gdf = wfs_client.fetch_layer(ds.dataset, ds.layer)
                if gdf.empty:
                    logger.warning("%s/%s: empty, skipping", ds.dataset, ds.layer)
                    continue
                extra = dict(ds.extra) if ds.extra else None
                transformed = transform_wfs_layer(
                    gdf,
                    ds.dataset,
                    ds.layer,
                    extra_columns=extra,
                )
                if first:
                    load_replace(
                        transformed,
                        ds.table,
                        engine,
                        geom_type=ds.geom_type,
                    )
                    first = False
                else:
                    load_append(
                        transformed,
                        ds.table,
                        engine,
                        geom_type=ds.geom_type,
                    )
                ok += 1
                logger.info("OK %s → %s", ds.key, ds.table)
            except Exception:
                fail += 1
                logger.error(
                    "FAIL %s (%s/%s):\n%s",
                    ds.key,
                    ds.dataset,
                    ds.layer,
                    traceback.format_exc(),
                )
    return ok, fail


def _run_gtfs(catalog: Catalog) -> tuple[int, int]:
    if not catalog.gtfs.enabled:
        return 0, 0
    ok, fail = 0, 0
    try:
        client = VbbGtfsClient()
        tables = client.fetch_feed(catalog.gtfs.feed_url)
        outputs = transform_gtfs(tables)

        # Order matters: routes before route_shapes (FK).
        load_dataframe(outputs["transit_routes"], "transit_routes", engine)
        ok += 1
        # Geopandas' default COPY-based writer can't serialize Python lists
        # into Postgres array literals — pre-format them as text.
        stops = outputs["transit_stops"].copy()
        stops["modes_served"] = stops["modes_served"].apply(
            lambda v: to_pg_array(v, kind="int")
        )
        stops["lines_served"] = stops["lines_served"].apply(
            lambda v: to_pg_array(v, kind="text")
        )
        load_replace(
            stops,
            "transit_stops",
            engine,
            geom_type="Point",
        )
        ok += 1
        load_replace(
            outputs["transit_route_shapes"],
            "transit_route_shapes",
            engine,
            geom_type="LineString",
        )
        ok += 1
        logger.info("OK gtfs → transit_stops, transit_routes, transit_route_shapes")
    except Exception:
        fail += 1
        logger.error("FAIL gtfs:\n%s", traceback.format_exc())
    return ok, fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="geo_context.run",
        description="Run the Berlin geo-context ETL pipeline.",
    )
    parser.add_argument(
        "--only",
        type=str,
        help="comma-separated list of dataset keys to run (default: all enabled)",
    )
    parser.add_argument(
        "--skip-gtfs", action="store_true", help="skip the GTFS pipeline"
    )
    parser.add_argument(
        "--skip-wfs", action="store_true", help="skip every WFS dataset"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    only = set(args.only.split(",")) if args.only else None
    catalog = load_catalog()

    wfs_ok, wfs_fail = (0, 0)
    if not args.skip_wfs:
        wfs_ok, wfs_fail = _run_wfs(catalog, only, BerlinGdiWfsClient())

    gtfs_ok, gtfs_fail = (0, 0)
    if not args.skip_gtfs and (only is None or "gtfs" in only):
        gtfs_ok, gtfs_fail = _run_gtfs(catalog)

    total_ok = wfs_ok + gtfs_ok
    total_fail = wfs_fail + gtfs_fail
    logger.info(
        "geo_context: %d ok, %d failed (wfs=%d/%d, gtfs=%d/%d)",
        total_ok,
        total_fail,
        wfs_ok,
        wfs_ok + wfs_fail,
        gtfs_ok,
        gtfs_ok + gtfs_fail,
    )
    return 1 if total_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
