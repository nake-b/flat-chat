"""Unit tests for `platinum.embed` — the Jina embedding client + batch UPSERT.

No network and no DB: the Jina HTTP call is served by an `httpx.MockTransport`
(injected by patching `httpx.Client` so even the client `JinaClient` builds
internally uses it), and the SQLAlchemy `Connection` is a tiny fake that
records UPSERTs + commits. Covers the hardening added in the PR-review follow-up:
retry/backoff, per-batch commit, response-order safety, and the failure modes
that must raise rather than corrupt data.
"""

from __future__ import annotations

import json

import httpx
import pytest

import platinum.embed as embed

EMBED_DIM = embed.EMBED_DIM


def _ok_response(request: httpx.Request, *, dim: int = EMBED_DIM) -> httpx.Response:
    """A well-formed Jina response: one item per input, each embedding's first
    element encodes the input's position so callers can verify mapping."""
    inputs = json.loads(request.content)["input"]
    data = [
        {"index": i, "embedding": [float(i)] + [0.0] * (dim - 1)}
        for i in range(len(inputs))
    ]
    return httpx.Response(200, json={"data": data})


def _install_transport(monkeypatch, handler) -> list[int]:
    """Patch `httpx.Client` so JinaClient's internal client uses `handler`.
    Returns a one-element list counting how many clients were constructed."""
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    constructed = [0]

    def fake_client(**kwargs):
        constructed[0] += 1
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(embed.httpx, "Client", fake_client)
    return constructed


class _FakeConn:
    """Minimal stand-in for a SQLAlchemy Connection used by embed_pending."""

    def __init__(self, rows):
        self._rows = rows
        self.upserts: list[dict] = []
        self.commits = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "INSERT INTO listings_embeddings" in sql:
            self.upserts.append(params)
            return None
        # The pending-rows SELECT.
        return iter(self._rows)

    def commit(self):
        self.commits += 1


# ---------------------------------------------------------------------------
# JinaClient construction + embed()
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        embed.JinaClient()


def test_embed_maps_vectors_in_input_order_despite_reordered_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        # Return data in REVERSE index order to prove embed() sorts by `index`.
        data = [
            {"index": i, "embedding": [float(i)] + [0.0] * (EMBED_DIM - 1)}
            for i in reversed(range(len(inputs)))
        ]
        return httpx.Response(200, json={"data": data})

    _install_transport(monkeypatch, handler)
    with embed.JinaClient() as client:
        vectors = client.embed(["a", "b", "c"])

    # vectors[k][0] must equal k — i.e. the k-th vector maps to the k-th input.
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]


def test_embed_sends_retrieval_passage_task(monkeypatch):
    # Documents must be embedded with the `retrieval.passage` LoRA so they pair
    # with `retrieval.query` at search time (Jina v3 is asymmetric).
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["task"] = body.get("task")
        return _ok_response(request)

    _install_transport(monkeypatch, handler)
    with embed.JinaClient() as client:
        client.embed(["a"])

    assert seen["task"] == "retrieval.passage"


def test_embed_raises_on_dim_mismatch(monkeypatch):
    _install_transport(monkeypatch, lambda req: _ok_response(req, dim=512))
    with embed.JinaClient() as client, pytest.raises(RuntimeError, match="dim"):
        client.embed(["a", "b"])


def test_embed_retries_transient_429_then_succeeds(monkeypatch):
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        if calls[0] == 1:
            # Retry-After: 0 keeps the test fast (no real backoff sleep).
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _ok_response(request)

    _install_transport(monkeypatch, handler)
    with embed.JinaClient() as client:
        vectors = client.embed(["a"])

    assert calls[0] == 2  # one failure, one success
    assert len(vectors) == 1


def test_embed_does_not_retry_non_retryable_401(monkeypatch):
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(401, json={"detail": "bad key"})

    _install_transport(monkeypatch, handler)
    with embed.JinaClient() as client, pytest.raises(httpx.HTTPStatusError):
        client.embed(["a"])

    assert calls[0] == 1  # 401 is fatal — no retry


# ---------------------------------------------------------------------------
# embed_pending — client reuse + per-batch commit + cardinality guard
# ---------------------------------------------------------------------------


def test_embed_pending_reuses_one_client_and_commits_per_batch(monkeypatch):
    constructed = _install_transport(monkeypatch, _ok_response)
    rows = [(f"id-{i}", f"Title {i}", f"Desc {i}") for i in range(5)]
    conn = _FakeConn(rows)

    embedded, skipped = embed.embed_pending(conn, batch_size=2)

    assert embedded == 5
    assert skipped == 0
    assert constructed[0] == 1  # one JinaClient reused across all batches
    assert len(conn.upserts) == 5
    assert conn.commits == 3  # ceil(5 / 2) batches, one commit each


def test_embed_pending_raises_on_cardinality_mismatch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # Return one FEWER vector than requested → strict zip must raise.
        inputs = json.loads(request.content)["input"]
        data = [
            {"index": i, "embedding": [0.0] * EMBED_DIM}
            for i in range(len(inputs) - 1)
        ]
        return httpx.Response(200, json={"data": data})

    _install_transport(monkeypatch, handler)
    rows = [(f"id-{i}", f"Title {i}", f"Desc {i}") for i in range(3)]
    conn = _FakeConn(rows)

    with pytest.raises(ValueError):
        embed.embed_pending(conn, batch_size=3)
