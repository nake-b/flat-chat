"""Unit test for `SearchService._embed` — the query-embedding call.

No DB, no network: a fake embedder records how it was called. Guards the
contract that `_embed` passes the REQUIRED keyword-only `input_type="query"`
to `Embedder.embed` (and picks the first vector from the result). Omitting
`input_type` raises `TypeError` at call time, which aborts the entire agent
run — exactly the regression this test exists to catch.
"""

from __future__ import annotations

import asyncio

from flat_chat.search.service import SearchService


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def embed(self, inputs, *, input_type, settings=None):
        # Mirrors pydantic_ai's keyword-only `input_type` signature so a missing
        # kwarg fails here the same way it would against the real Embedder.
        self.calls.append((list(inputs), input_type))
        return [[0.1] * 1024 for _ in inputs]


def test_embed_passes_query_input_type_and_returns_first_vector():
    fake = _FakeEmbedder()
    svc = SearchService(db=None, embedder=fake)  # _embed doesn't touch db

    vector = asyncio.run(svc._embed("vibrant neighbourhood"))

    assert fake.calls == [(["vibrant neighbourhood"], "query")]
    assert len(vector) == 1024
