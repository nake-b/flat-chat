from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL

# The ingestion service owns the `world` schema. Pin search_path on every
# connection so all raw SQL, TRUNCATE/INSERT, pandas `.to_sql`/`.to_postgis`,
# and reflection resolve unqualified table names to `world` — with `public`
# kept on the path for the postgis/vector types and the listings updated_at
# trigger function. This is the single seam that schema-qualifies the entire
# pipeline (silver/gold/platinum/geo_context). Backend uses explicit
# `{"schema": "world"}` instead — see schema-ownership-split.md.
engine = create_engine(
    DATABASE_URL,
    connect_args={"options": "-csearch_path=world,public"},
)
SessionLocal = sessionmaker(bind=engine)

_metadata = MetaData()
_reflected = False


def _ensure_reflected():
    global _reflected
    if not _reflected:
        _metadata.reflect(bind=engine)
        _reflected = True


def get_table(name: str) -> Table:
    _ensure_reflected()
    return _metadata.tables[name]


def get_session() -> Session:
    return SessionLocal()
