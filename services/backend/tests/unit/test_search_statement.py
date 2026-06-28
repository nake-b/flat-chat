"""Unit tests for ``SearchService`` statement composition.

These build the SELECT and force SQLAlchemy's cache-key pass WITHOUT a
database — they catch compile-time mistakes that ``stmt.compile()`` alone
would miss, in milliseconds, under bare ``pytest`` (no ``TEST_DATABASE_URL``).

Regression (June 2026): the proximity branch in ``_apply_listing_filters``
passed ``type_=bool`` to ``ST_DWithin``. SQLAlchemy's ``to_instance``
instantiates a callable type argument — ``bool()`` returns ``False`` — so the
function's type became the value ``False``. The first statement-caching pass
then did ``False._static_cache_key`` and raised
``AttributeError: 'bool' object has no attribute '_static_cache_key'`` on every
``near_lat``/``near_lon`` search. The fix is ``type_=Boolean``. Asserting
``_generate_cache_key()`` succeeds is exactly the operation that blew up.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from flat_chat.search.schemas import SearchParams
from flat_chat.search.service import SearchService


def _count_stmt(params: SearchParams):
    """Build the COUNT statement — the cheapest path through the shared
    ``_apply_listing_filters`` (where the ST_DWithin proximity branch lives)."""
    service = SearchService(db=AsyncMock(), embedder=None)
    return service._count_statement(params)


def test_proximity_statement_generates_cache_key():
    """The ST_DWithin proximity branch must survive the cache-key pass."""
    stmt = _count_stmt(
        SearchParams(near_lat=52.52, near_lon=13.405, radius_km=1.0, sort_by="recent")
    )
    # This is the call that raised before the type_=bool -> Boolean fix.
    assert stmt._generate_cache_key() is not None


def test_district_statement_generates_cache_key():
    """The placeholder-agent path (district filter only) also caches cleanly."""
    stmt = _count_stmt(SearchParams(districts=["Kreuzberg"], sort_by="recent"))
    assert stmt._generate_cache_key() is not None


class _RecordingEmbedder:
    """Fake Embedder that records how ``embed`` was invoked.

    Regression (June 2026): ``SearchService._embed`` called
    ``embedder.embed([query])`` without the now-required ``input_type``
    keyword, raising ``TypeError: Embedder.embed() missing 1 required
    keyword-only argument: 'input_type'``. The query side of Jina v3's
    asymmetric retrieval must pass ``input_type="query"``.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def embed(self, inputs, *, input_type, settings=None):
        self.calls.append({"inputs": inputs, "input_type": input_type})
        # Indexable like a real EmbeddingResult: result[0] -> a vector.
        return [[0.0] * 1024]


def test_embed_passes_input_type_query():
    """A semantic query must embed with input_type='query'."""
    embedder = _RecordingEmbedder()
    service = SearchService(db=AsyncMock(), embedder=embedder)

    vector = asyncio.run(service._embed("quiet flat near a park"))

    assert embedder.calls == [
        {"inputs": ["quiet flat near a park"], "input_type": "query"}
    ]
    assert len(vector) == 1024
