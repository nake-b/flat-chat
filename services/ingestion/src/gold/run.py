"""CLI orchestrator for the gold layer.

Populates `listings_geo_context` (scalar / field facts) + six junction
tables (`listings_nearby_transit`, `listings_nearby_schools`,
`listings_nearby_hospitals`, `listings_nearby_parks`,
`listings_nearby_playgrounds`, `listings_nearby_water`) — see
`agent-compound-docs/decisions/spatial-neighbor-tables.md`.

Usage:
    python -m gold.run                                 # every family
    python -m gold.run --only nearby_transit,noise     # subset
    python -m gold.run --only chip_scalars             # rebuild card chips only

Each chip family runs in its own ``engine.begin()`` block, so a failure in
one family (e.g. greenery computation hits a bad geometry) doesn't abort
the others. Exit code 1 if any family failed.

Idempotent and set-based: every chip function is a single bulk SQL
UPSERT against `listings_geo_context`, joining `listings.location` to a
silver geo-context table. Running it twice yields the same result.

Prerequisites: silver listings + silver geo-context tables must exist
and be non-empty. The run fails fast with a clear message if not — we
don't want to silently produce an empty gold table.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from db import engine
from sqlalchemy import text

from . import enrich_listings as gold

logger = logging.getLogger(__name__)


# Required silver tables. If any are empty, gold can't produce a useful
# enrichment — fail fast rather than silently writing NULLs everywhere.
REQUIRED_TABLES: list[str] = [
    "listings",
    "transit_stops",
    "parks",
    "schools",
    "school_catchments",
    "hospitals",
    "water_bodies",
    "street_noise_2022",
    "population_density_2025",
    "social_monitoring_2025",
    "playgrounds",
    "disabled_parking",
]


def _check_prerequisites() -> tuple[bool, list[str]]:
    """Return (ok, list_of_empty_tables). Logs each empty table at WARNING."""
    empty: list[str] = []
    with engine.connect() as conn:
        for table in REQUIRED_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            if count == 0:
                empty.append(table)
                logger.warning(
                    "Prerequisite check: %s is empty — gold output will be incomplete",
                    table,
                )
    # Listings is the only hard requirement. Other tables can be empty
    # (chip values for those families just stay NULL) but we still warn.
    listings_present = "listings" not in empty
    return listings_present, empty


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gold.run",
        description="Enrich listings with pre-joined geo-context (gold layer).",
    )
    parser.add_argument(
        "--only",
        type=str,
        help=(
            "comma-separated chip families to run (default: all). "
            "Valid: " + ", ".join(gold.CHIP_FAMILIES.keys())
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    # Sanity-check prerequisites — empty listings table means gold has
    # nothing to enrich and we should fail visibly instead of producing
    # an empty table.
    ok, empty_tables = _check_prerequisites()
    if not ok:
        logger.error(
            "Cannot run gold: required `listings` table is empty. "
            "Run silver.run first."
        )
        return 1

    requested: set[str] | None = (
        {k.strip() for k in args.only.split(",")} if args.only else None
    )
    if requested is not None:
        unknown = requested - set(gold.CHIP_FAMILIES.keys())
        if unknown:
            logger.error(
                "Unknown chip families: %s. Valid: %s",
                ", ".join(sorted(unknown)),
                ", ".join(gold.CHIP_FAMILIES.keys()),
            )
            return 1

    families = (
        [(name, fn) for name, fn in gold.CHIP_FAMILIES.items() if name in requested]
        if requested is not None
        else list(gold.CHIP_FAMILIES.items())
    )

    # Seed rows first — every per-chip UPDATE needs a row in lgc to update.
    # Wrapped in its own transaction so a chip failure doesn't leave new
    # listings without a gold row.
    try:
        with engine.begin() as conn:
            seeded = conn.execute(
                text(
                    """
                    INSERT INTO listings_geo_context (listing_id)
                    SELECT l.id
                    FROM listings l
                    LEFT JOIN listings_geo_context lgc ON lgc.listing_id = l.id
                    WHERE lgc.listing_id IS NULL
                      AND l.location IS NOT NULL
                    ON CONFLICT (listing_id) DO NOTHING
                    """
                )
            ).rowcount
            logger.info("OK seed → listings_geo_context (%d new rows)", seeded or 0)
    except Exception:
        logger.error("FAIL seed (rolled back):\n%s", traceback.format_exc())
        return 1

    ok_count, fail_count = 0, 0
    for name, fn in families:
        try:
            with engine.begin() as conn:
                rows = fn(conn)
            ok_count += 1
            logger.info("OK %s → listings_geo_context (%d rows touched)", name, rows)
        except Exception:
            fail_count += 1
            logger.error("FAIL %s (rolled back):\n%s", name, traceback.format_exc())

    logger.info(
        "gold: %d ok, %d failed (of %d families)",
        ok_count,
        fail_count,
        len(families),
    )
    if empty_tables:
        logger.warning(
            "Note: silver tables were empty during this run: %s",
            ", ".join(empty_tables),
        )
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
