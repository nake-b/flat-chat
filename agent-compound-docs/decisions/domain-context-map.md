# Domain context map

A strategic-DDD view of flat-chat: what the bounded contexts are and how they
relate. Written alongside the schema-ownership split, which *formalizes* the
first relationship below. This is a framing doc — the mechanics live in
`schema-ownership-split.md`, `listings-domain-module.md`, and
`agent-vs-http-data-flow.md`.

## Contexts

**1. Apartment context** — the observed world: listings + their geo-context
(transit, schools, kitas, parks, noise, landmarks, Bezirk/Ortsteil, …). Spans the ingestion medallion
(iron→platinum) and the backend's read side (`listings/`, `search/`).

**2. Assistant / Conversation context** — `chat/`. Its own ubiquitous language —
Session, turn, tool, `LlmResultSetView`, result *tiers* — about *conversation*,
not apartments. Consumes the apartment context downstream.

**3. Frontend** — the React SPA. Mirrors the backend's `SessionState`.

> Note: `listings/`, `search/`, `chat/` are sometimes described as three
> contexts. They are not — `listings/` and `search/` share the apartment
> context's language; they're *layers*. The real seam is **Apartment ↔
> Assistant**.

## Relationships (context map)

- **Apartment context is a Shared Kernel** between ingestion (writer) and backend
  (reader). The schema-ownership split makes this explicit: the kernel is the
  **`world` schema**. Ingestion owns the authoritative DDL; the backend keeps
  read-only ORM over it. **The kernel's enforced contract is the drift test**
  (`tests/integration/test_world_schema_drift.py`) — it fails if the backend's
  ORM and the live `world` schema diverge.

- **Anti-corruption layer** at the kernel's upstream edge: the **silver
  transformers** (`services/ingestion/src/silver/sources/`) normalize each
  portal's raw JSON into the canonical `Listing`, and the geo-context transforms
  (`geo_context/transform/wfs.py` + `transform/aliases.py`) translate German
  source column names into the English domain vocabulary (e.g. the bezirke
  layer's `namgem` → `name`). Raw portal/German data never escapes silver.

- **Assistant context** depends on the Apartment context (reads `Listing`,
  `ListingCard`, `ListingDetail`); the dependency is one-way (`chat/` →
  `listings/`/`search/`, never the reverse).

- **Frontend is a Conformist** to the backend's `SessionState` — a hand-maintained
  TypeScript mirror, no ACL, no codegen. This is the highest-drift boundary in
  the system; hardening it (Pydantic→TS codegen) is a known, separate follow-up.

## Tactical note

The domain model is intentionally **anemic** (ORM data bags + DTOs; services are
repositories/projectors). Correct for a read/projection-heavy search app with few
invariants — there are no aggregate roots guarding cross-table consistency, and
that's deliberate, not a gap. Don't add aggregates/domain events here.
