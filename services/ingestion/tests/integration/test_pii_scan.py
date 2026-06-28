"""Integration tests: the loaders strip poster PII before it hits Postgres.

Runs the real bronze + iron loaders against a Postgres fixture, then executes
the same JSONB audit SQL used in the remediation verification. After loading
deliberately dirty records, the audit must find ZERO poster-PII paths — proving
the `strip_pii` choke-point in the loaders actually fires end-to-end (the class
of bug a compile-only assertion would miss). A positive-control row (inserted
raw, bypassing the loader) confirms the audit query itself detects PII.

Opt-in via ``TEST_DATABASE_URL`` so it never touches the canonical
``flat-chat-db``. Setup mirrors ``test_silver_deduplication.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

_TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TEST_URL,
    reason="TEST_DATABASE_URL not set — see test docstring for setup",
)

# Audit SQL — must return 0 once poster PII has been stripped.
_BRONZE_AUDIT = sa.text(
    """
    SELECT count(*) FROM raw_listings
    WHERE jsonb_path_exists(data, '$.dump.seller.name')
       OR jsonb_path_exists(data, '$.dump.seller.phone')
       OR jsonb_path_exists(data, '$.dump.embeddedState')
       OR jsonb_path_exists(data, '$.dump.lister.name')
       OR jsonb_path_exists(data, '$.dump.lister.memberSince')
       OR jsonb_path_exists(data, '$.dump.lister.online')
       OR jsonb_path_exists(data, '$.dump.entity.advertiser')
       OR jsonb_exists_any(
            data #> '{dump,advertiser}',
            array['name', 'firstName', 'photo', 'profile', 'phone', 'email']
          )
    """
)
_IRON_AUDIT = sa.text(
    """
    SELECT count(*) FROM iron_cards
    WHERE jsonb_path_exists(data, '$.posterName')
       OR jsonb_path_exists(data, '$.onlineSince')
       OR jsonb_path_exists(data, '$.raw_payload.card.seller_name')
       OR jsonb_path_exists(data, '$.raw_payload.detail.seller')
       OR jsonb_path_exists(data, '$.raw_payload.detail.sellerProfileHref')
       OR jsonb_path_exists(data, '$.raw_payload.detail.embeddedStateSnippets')
       OR jsonb_path_exists(data, '$.raw_payload.scripts_or_state')
    """
)


def _refuse_canonical(url: str) -> None:
    if "flat-chat-db" in url:
        pytest.fail("Refusing to run against canonical 'flat-chat-db'.")


def _bootstrap(url: str) -> None:
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS world CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS app CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(sa.text("CREATE SCHEMA world"))
        conn.execute(sa.text("CREATE SCHEMA app"))
    engine.dispose()


def _alembic_config(url: str) -> Config:
    ingestion_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(ingestion_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(ingestion_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    _refuse_canonical(_TEST_URL)
    os.environ["DATABASE_URL"] = _TEST_URL  # db.py / env.py read this
    _bootstrap(_TEST_URL)
    command.upgrade(_alembic_config(_TEST_URL), "head")
    eng = sa.create_engine(
        _TEST_URL, connect_args={"options": "-csearch_path=world,public"}
    )
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine: sa.Engine) -> Session:
    with Session(engine) as s:
        s.execute(sa.text("TRUNCATE world.raw_listings, world.iron_cards CASCADE"))
        s.commit()
        yield s


@pytest.fixture()
def loaders_on_test_db(engine: sa.Engine, monkeypatch: pytest.MonkeyPatch):
    """Point the loaders' `get_table` at the test engine.

    The loaders resolve tables via `db.get_table`, whose module-global engine
    binds to ``DATABASE_URL`` at import — unreliable here, since another test
    may import `db` first with the default (in-compose) host. Reflecting from
    the test engine instead keeps these tests independent of import order.
    """
    import bronze.loader as bronze_loader
    import iron.loader as iron_loader

    md = sa.MetaData()
    md.reflect(bind=engine, only=["raw_listings", "iron_cards"])
    get_table = md.tables.__getitem__

    monkeypatch.setattr(bronze_loader, "get_table", get_table)
    monkeypatch.setattr(iron_loader, "get_table", get_table)


_TS = "2026-01-01T00:00:00Z"

_DIRTY_BRONZE = [
    {
        "id": "ka-1",
        "listing_source": "kleinanzeigen",
        "scrapeUrl": "https://example.test/ka-1",
        "scrapedAt": _TS,
        "dump": {
            "title": "Helles 2-Zi",
            "seller": {
                "name": "Hans M",
                "type": "Privater Nutzer",
                "phone": "0151 26376735",
            },
            "embeddedState": ["window.__INITIAL_STATE__ = {}"],
        },
    },
    {
        "id": "wg-1",
        "listing_source": "wg-gesucht",
        "scrapeUrl": "https://example.test/wg-1",
        "scrapedAt": _TS,
        "dump": {
            "lister": {
                "name": "E. Peics",
                "type": "private",
                "memberSince": "2021",
                "online": "3h",
            }
        },
    },
    {
        "id": "ha-1",
        "listing_source": "housinganywhere",
        "scrapeUrl": "https://example.test/ha-1",
        "scrapedAt": _TS,
        "dump": {
            "advertiser": {"name": "Jane", "type": "agency", "photo": "u"},
            "entity": {"id": 7, "price": 86500, "advertiser": {"name": "Jane"}},
        },
    },
]

_DIRTY_IRON = [
    {
        "listing_source": "kleinanzeigen",
        "external_id": "ka-1",
        "listing_url": "https://example.test/ka-1",
        "source_url": "https://example.test/ka",
        "scraped_at": _TS,
        "raw_payload": {
            "card": {"seller_name": "Hans M", "title": "Helles 2-Zi"},
            "detail": {
                "seller": "Hans M",
                "sellerProfileHref": "/s-bestandsliste.html?userId=1",
                "embeddedStateSnippets": ["dataLayer = {}"],
            },
            "scripts_or_state": ["window.__ = {}"],
        },
    },
    {
        "listing_source": "wg-gesucht",
        "id": "wg-1",
        "url": "https://example.test/wg-1",
        "scrapeUrl": "https://example.test/wg",
        "scrapedAt": _TS,
        "posterName": "E. Peics",
        "onlineSince": "3h",
    },
]


def _write(tmp_path: Path, name: str, records: list) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(records))
    return path


def test_bronze_loader_strips_poster_pii(
    session: Session, tmp_path: Path, loaders_on_test_db: None
) -> None:
    from bronze.loader import load_json

    load_json(_write(tmp_path, "bronze.json", _DIRTY_BRONZE), session)

    assert session.execute(sa.text("SELECT count(*) FROM raw_listings")).scalar() == 3
    assert session.execute(_BRONZE_AUDIT).scalar() == 0
    # The non-PII lister type is retained.
    ka = session.execute(
        sa.text(
            "SELECT data #>> '{dump,seller,type}' "
            "FROM raw_listings WHERE external_id = 'ka-1'"
        )
    ).scalar()
    assert ka == "Privater Nutzer"


def test_iron_loader_strips_poster_pii(
    session: Session, tmp_path: Path, loaders_on_test_db: None
) -> None:
    from iron.loader import load_json

    load_json(_write(tmp_path, "iron.json", _DIRTY_IRON), session)

    assert session.execute(sa.text("SELECT count(*) FROM iron_cards")).scalar() == 2
    assert session.execute(_IRON_AUDIT).scalar() == 0


def test_audit_sql_detects_unstripped_pii(session: Session) -> None:
    """Positive control: a raw (unstripped) row must trip the audit query."""
    session.execute(
        sa.text(
            "INSERT INTO raw_listings (source_name, external_id, data, scraped_at) "
            "VALUES ('kleinanzeigen', 'raw-1', CAST(:d AS jsonb), :t)"
        ),
        {"d": json.dumps(_DIRTY_BRONZE[0]), "t": _TS},
    )
    session.commit()
    assert session.execute(_BRONZE_AUDIT).scalar() == 1
