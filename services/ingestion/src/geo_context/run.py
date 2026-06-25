"""CLI orchestrator for the geo-context ETL.

Usage:
    python -m geo_context.run               # run every enabled dataset
    python -m geo_context.run --only schools,parks
    python -m geo_context.run --skip-gtfs   # WFS only

Each target table is wrapped in a single ``engine.begin()`` block so a
multi-layer family (e.g. ``hospitals = plan + other``) is all-or-nothing:
if the second layer fails after the first wrote, the whole family rolls
back instead of leaving the table half-populated. The GTFS triplet
(routes / stops / route_shapes) is wrapped the same way. A failure in one
table family does not abort other families. Exit code 1 if any family
failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from db import engine
from sqlalchemy import text

from .config import Catalog, WfsDataset, load_catalog
from .extract.gtfs import VbbGtfsClient
from .extract.wfs import BerlinGdiWfsClient
from .load.postgis import _write_append, _write_dataframe, _write_replace, to_pg_array
from .transform.gtfs import transform_gtfs
from .transform.wfs import transform_wfs_layer

logger = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    """Return True if target table exists in current schema."""
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _ensure_brandenburger_tor(conn) -> bool:
    """Insert synthetic Brandenburger Tor into buildings exactly once."""
    exists = bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM buildings
                    WHERE lower(trim(name)) = 'brandenburger tor'
                )
                """
            )
        ).scalar()
    )
    if exists:
        return False

    conn.execute(
        text(
            """
            INSERT INTO buildings (
                name,
                description,
                geom
            ) VALUES (
                'Brandenburger Tor',
                'synthetic_fallback',
                ST_Multi(
                    ST_GeomFromText(
                        'POLYGON((13.37746 52.51616,13.37795 52.51616,13.37795 52.51640,13.37746 52.51640,13.37746 52.51616))',
                        4326
                    )
                )
            )
            """
        )
    )
    return True


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
        # All layers feeding one target table commit together inside a single
        # transaction. A mid-family failure (e.g. hospitals_other after
        # hospitals_plan wrote) rolls back the partial state so the next run
        # starts from a clean previous-good snapshot.
        family_ok = 0
        family_fail_keys: list[str] = []
        try:
            with engine.begin() as conn:
                if not _table_exists(conn, table):
                    fail += len(entries)
                    family_fail_keys = [ds.key for ds in entries]
                    logger.error(
                        "SKIP table=%s (layers=%s): target table missing. "
                        "Run migrations before geo_context reload.",
                        table,
                        ",".join(family_fail_keys),
                    )
                    continue
                # Streaming write: first page of the first layer TRUNCATEs;
                # everything else appends in the same transaction. Memory
                # stays bounded to one page (10k rows) regardless of how big
                # the source layer is — the noise raster (3.8M points) would
                # otherwise materialise as a single ~3 GiB DataFrame and OOM
                # the container.
                table_initialized = False
                for ds in entries:
                    extra = dict(ds.extra) if ds.extra else None
                    layer_rows = 0
                    for page_gdf in wfs_client.iter_layer_pages(
                        ds.dataset, ds.layer
                    ):
                        if page_gdf.empty:
                            continue
                        transformed = transform_wfs_layer(
                            page_gdf,
                            ds.dataset,
                            ds.layer,
                            extra_columns=extra,
                        )
                        if not table_initialized:
                            _write_replace(
                                conn,
                                transformed,
                                ds.table,
                                geom_type=ds.geom_type,
                            )
                            table_initialized = True
                        else:
                            _write_append(
                                conn,
                                transformed,
                                ds.table,
                                geom_type=ds.geom_type,
                            )
                        layer_rows += len(transformed)
                    if ds.key == "alkis_buildings" and _ensure_brandenburger_tor(conn):
                        layer_rows += 1
                        logger.info(
                            "%s: injected synthetic fallback feature 'Brandenburger Tor'",
                            ds.key,
                        )
                    if layer_rows == 0:
                        logger.warning(
                            "%s/%s: empty, skipping", ds.dataset, ds.layer
                        )
                        continue
                    family_ok += 1
                    logger.info(
                        "OK %s → %s (%d rows)", ds.key, ds.table, layer_rows
                    )
            ok += family_ok
        except Exception:
            # Whole family rolled back — count every layer in the family as
            # failed so the summary line tells the truth.
            fail += len(entries)
            family_fail_keys = [ds.key for ds in entries]
            logger.error(
                "FAIL table=%s (layers=%s) — rolled back:\n%s",
                table,
                ",".join(family_fail_keys),
                traceback.format_exc(),
            )
    return ok, fail


def _run_gtfs(catalog: Catalog) -> tuple[int, int]:
    if not catalog.gtfs.enabled:
        return 0, 0
    try:
        client = VbbGtfsClient()
        tables = client.fetch_feed(catalog.gtfs.feed_url)
        outputs = transform_gtfs(tables)

        # All three transit_* tables commit together. They're a foreign-key
        # family (routes ← route_shapes) and the agent's tools assume the
        # set is internally consistent — never ship one without the others.
        with engine.begin() as conn:
            # Order matters: routes before route_shapes (FK).
            _write_dataframe(conn, outputs["transit_routes"], "transit_routes")
            # Geopandas' default COPY-based writer can't serialize Python
            # lists into Postgres array literals — pre-format them as text.
            stops = outputs["transit_stops"].copy()
            stops["modes_served"] = stops["modes_served"].apply(
                lambda v: to_pg_array(v, kind="int")
            )
            stops["lines_served"] = stops["lines_served"].apply(
                lambda v: to_pg_array(v, kind="text")
            )
            _write_replace(conn, stops, "transit_stops", geom_type="Point")
            _write_replace(
                conn,
                outputs["transit_route_shapes"],
                "transit_route_shapes",
                geom_type="LineString",
            )
        logger.info("OK gtfs → transit_stops, transit_routes, transit_route_shapes")
        return 3, 0
    except Exception:
        logger.error("FAIL gtfs (rolled back):\n%s", traceback.format_exc())
        return 0, 3


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

    # Chain gold re-enrichment if any silver geo-context family succeeded.
    # When geo-context refreshes (e.g. updated noise raster / new Kitas),
    # every listing's gold row needs to be re-computed against the new data
    # — chaining here ensures the agent's search results reflect the
    # refreshed truth without manual intervention. Skipped if everything
    # failed (no point re-enriching against the previous-good snapshot;
    # last run's gold is still valid).
    if total_ok > 0:
        logger.info("geo_context: chaining gold re-enrichment ...")
        from gold.run import main as gold_main

        gold_rc = gold_main([])
        if gold_rc != 0:
            logger.warning("geo_context→gold chain returned non-zero (%d)", gold_rc)

    return 1 if total_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
