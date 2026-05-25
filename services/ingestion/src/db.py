from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
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
