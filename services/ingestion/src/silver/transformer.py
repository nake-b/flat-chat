"""Silver dispatcher: routes bronze rows to per-source transformers and upserts."""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_table

from .sources import housinganywhere, kleinanzeigen, wg_gesucht, wohninberlin
from .upsert import conflict_update_set

logger = logging.getLogger(__name__)

_TRANSFORMERS = {
    "wg-gesucht": wg_gesucht.to_listing_row,
    "kleinanzeigen": kleinanzeigen.to_listing_row,
    "housinganywhere": housinganywhere.to_listing_row,
    "wohninberlin": wohninberlin.to_listing_row,
}


def transform(session: Session) -> int:
    """Read all bronze rows, route by source, upsert into silver.

    Returns the number of rows upserted.
    """
    raw_listings = get_table("raw_listings")
    listings = get_table("listings")

    rows = session.execute(select(raw_listings)).mappings().all()

    count = 0
    skipped: dict[str, int] = {}
    for raw in rows:
        source = raw["source_name"]
        fn = _TRANSFORMERS.get(source)
        if fn is None:
            skipped[source] = skipped.get(source, 0) + 1
            continue

        values = fn(dict(raw))
        values["raw_listing_id"] = raw["id"]
        values["source_name"] = source
        values["external_id"] = raw["external_id"]
        values["scraped_at"] = raw["scraped_at"]

        # Keep the PostGIS `location` Point in sync with `latitude`/`longitude`
        # on every write. Migration 0002 did a one-shot historical backfill,
        # but new listings without this would skip the gold layer entirely
        # (gold queries `location`, not lat/lng). The expression evaluates
        # at INSERT time so it captures whatever lat/lng the transformer set.
        lat = values.get("latitude")
        lon = values.get("longitude")
        if lat is not None and lon is not None:
            values["location"] = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)

        stmt = pg_insert(listings).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_listing_source_external",
            set_=conflict_update_set(values),
        )
        session.execute(stmt)
        count += 1

    session.commit()

    if skipped:
        for src, n in skipped.items():
            logger.warning("skipped %d rows from unknown source: %r", n, src)

    return count


# One window-function DELETE collapses duplicate flats — same title + address,
# any source — down to a single survivor. Companies repost the same apartment
# with a fresh `external_id` (often just a new price), so the UPSERT key
# `(source_name, external_id)` lets each repost in as its own row; this is the
# second pass that removes them. Run AFTER `transform` and BEFORE gold so deleted
# rows never get enriched. It must run every silver.run, not just once: bronze
# `raw_listings` rows survive a silver delete (FK is ON DELETE SET NULL), and
# `transform` reprocesses all of bronze, so a one-off cleanup would be re-undone
# on the next run.
#
# Survivor = the row that still carries coordinates (so it stays on the map / in
# search), then newest `scraped_at`; `ingested_at`/`id` only break ties for
# determinism. Only rows with a non-blank title AND address participate — NULL or
# empty values must never collapse together.
_DEDUP_SQL = text(
    """
    DELETE FROM listings
    WHERE id IN (
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY btrim(title), btrim(address)
                       ORDER BY (location IS NOT NULL) DESC,
                                scraped_at DESC, ingested_at DESC, id DESC
                   ) AS rn
            FROM listings
            WHERE title IS NOT NULL AND btrim(title) <> ''
              AND address IS NOT NULL AND btrim(address) <> ''
        ) ranked
        WHERE rn > 1
    )
    """
)


def deduplicate(session: Session) -> int:
    """Delete duplicate listings (same title + address, any source), keeping one.

    Returns the number of rows deleted. Idempotent — a second call deletes 0.
    """
    result = session.execute(_DEDUP_SQL)
    session.commit()
    return result.rowcount
