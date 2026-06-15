"""Platinum layer — vector embeddings for semantic search.

Stores 1024-dim Jina v3 embeddings of `listings.title + description` in
the `listings_embeddings` table. Separated from silver listings because:
  - embeddings are a per-entity transformation; swapping models shouldn't
    require a schema migration on `listings`
  - the HNSW index is hot only for semantic-ranked queries; keeping it on
    its own table isolates the I/O profile

Run cadence:
  - Chained after `silver.run` for new listings (no embedding yet)
  - Standalone via `python -m platinum.run` to backfill or re-embed when
    swapping models

Architecture-decision doc: `agent-compound-docs/decisions/gold-platinum-layers.md`
"""
