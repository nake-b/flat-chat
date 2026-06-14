"""Silver dispatcher: routes bronze rows to per-source transformers and upserts."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_table

from .sources import kleinanzeigen, wg_gesucht

logger = logging.getLogger(__name__)

_TRANSFORMERS = {
    "wg-gesucht": wg_gesucht.to_listing_row,
    "kleinanzeigen": kleinanzeigen.to_listing_row,
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

        stmt = pg_insert(listings).values(**values)
        update_set = {k: v for k, v in values.items() if k not in ("source_name", "external_id")}
        stmt = stmt.on_conflict_do_update(
            constraint="uq_listing_source_external",
            set_=update_set,
        )
        session.execute(stmt)
        count += 1

    session.commit()

    if skipped:
        for src, n in skipped.items():
            logger.warning("skipped %d rows from unknown source: %r", n, src)

    return count
