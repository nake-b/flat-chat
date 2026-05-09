import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import get_table


def load_json(path: Path, session: Session) -> int:
    """Load a scraped JSON file into the raw_listings (bronze) table.

    Returns the number of rows upserted.
    """
    raw_listings = get_table("raw_listings")

    with open(path) as f:
        records = json.load(f)

    count = 0
    for record in records:
        source_name = record.get("listing_source", "unknown")
        external_id = str(record["id"])

        stmt = pg_insert(raw_listings).values(
            source_name=source_name,
            source_url=record.get("scrapeUrl"),
            external_id=external_id,
            data=record,
            scraped_at=record["scrapedAt"],
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_raw_source_external",
            set_={
                "data": stmt.excluded.data,
                "scraped_at": stmt.excluded.scraped_at,
                "source_url": stmt.excluded.source_url,
            },
        )
        session.execute(stmt)
        count += 1

    session.commit()
    return count
