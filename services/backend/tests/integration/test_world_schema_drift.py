"""Drift guard: backend read-models ↔ the live `world` schema.

This is the cross-service contract for the shared kernel. The ingestion
service owns the authoritative DDL for the `world` schema; the backend keeps
read-only ORM classes pointed at it (flat_chat.listings.models). Nothing
mechanically stops the two diverging — a renamed/dropped column in an ingestion
migration would leave the backend's ORM asserting a column Postgres no longer
has, the classic "compiles in SQLAlchemy but Postgres rejects at runtime" bug.

So we reflect the LIVE `world` schema (built by ingestion's Alembic in the
``schema_at_head`` fixture) and assert every backend ORM table + column exists
in it. We do NOT require equality — the backend reads a subset — only that
everything the backend *claims* is really there.

See agent-compound-docs/decisions/schema-ownership-split.md
and agent-compound-docs/decisions/domain-context-map.md.
"""

from __future__ import annotations

import sqlalchemy as sa

from flat_chat.core.database import Base

from ..conftest import DB_REQUIRED


@DB_REQUIRED
def test_backend_orm_matches_live_world_schema(
    test_db_url: str, schema_at_head: None
) -> None:
    engine = sa.create_engine(test_db_url)
    inspector = sa.inspect(engine)
    try:
        live_world_tables = set(inspector.get_table_names(schema="world"))

        problems: list[str] = []
        checked = 0
        for table in Base.metadata.sorted_tables:
            if table.schema != "world":
                continue
            checked += 1
            if table.name not in live_world_tables:
                problems.append(f"missing table: world.{table.name}")
                continue
            live_cols = {
                c["name"] for c in inspector.get_columns(table.name, schema="world")
            }
            orm_cols = {c.name for c in table.columns}
            absent = orm_cols - live_cols
            if absent:
                problems.append(
                    f"world.{table.name}: ORM columns absent in DB: {sorted(absent)}"
                )
    finally:
        engine.dispose()

    # Guard against the test silently passing because nothing was world-scoped.
    assert checked >= 9, f"expected ≥9 world-schema ORM tables, checked {checked}"
    assert not problems, "Backend ORM ↔ world schema drift:\n  " + "\n  ".join(problems)
