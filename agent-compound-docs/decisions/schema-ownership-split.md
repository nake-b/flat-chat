# Schema-ownership split — `world` / `app`

**Status:** Implemented.
**Supersedes:** the planning sketch in `PLAN-schema-ownership-split.md` (which
predates the `world`/`app` naming and the decisions below).

## Context

The backend's Alembic env originally owned **all** migrations — including the
medallion (iron→platinum) and geo-context tables that the **ingestion** service
is the sole writer of. The backend even defined `IronCard`/`RawListing` ORM
classes it never queried, purely so the mapper resolved `Listing`'s FK and
autogenerate saw the full schema. One database, two writers, one migration
owner — the wrong service owned the schema.

## Decision

**Two Postgres schemas in one database**, each owned by the service that is its
source of truth, with a hard cross-schema FK preserved for the future.

| Schema | Holds | Owner | Migrated by |
|--------|-------|-------|-------------|
| `world` | iron_cards, raw_listings, listings, listings_geo_context, listings_embeddings, listings_nearby_* (×8: transit/schools/hospitals/parks/playgrounds/water/kitas/landmarks), transit_*, schools, school_catchments, parks, playgrounds, hospitals, disabled_parking, kitas, landmarks, bezirke, ortsteile, inner_city_zone, strategic_noise_2022, green_volume_2020, population_density_2025, water_bodies, + the `named_places` VIEW | **ingestion** | `services/ingestion/alembic/` → `world.alembic_version` |
| `app` | users, sessions, bookmarks (+ future) — **none built yet** | **backend** | `services/backend/alembic/` → `app.alembic_version` |

- **Naming — `world` + `app`.** `world` = the observed reference data we ingest
  and only read (source of truth lives upstream); `app` = our product's own
  state. Chosen over `ingestion`/`app` (asymmetric: pipeline vs role), `external`
  (FDW connotation), `reference`, `upstream`. Names the bounded context, not the
  pipeline; neither is a reserved word.
- **Bootstrap is owned by the postgres service.** `services/postgres/init/01-bootstrap.sql`
  (mounted at `/docker-entrypoint-initdb.d/`) creates the `postgis` + `vector`
  extensions **in `public`** (DB-global; types must resolve for `world.*`
  regardless of search_path) and the `world` + `app` schemas. Extensions were
  removed from migrations 0001/0002. For existing volumes (init SQL only runs on
  a fresh volume), `scripts/bootstrap-schemas.sh` applies the same idempotently.
- **ingestion resolves `world` via search_path.** All ingestion SQL is raw
  (`op.execute`, `to_sql`/`to_postgis`, hand-written enrich SQL). Rather than
  qualify every statement, the single shared engine (`services/ingestion/src/db.py`)
  and the ingestion Alembic env pin `search_path = world, public` via libpq
  `connect_args` (NOT an in-band `SET`, which autobegins a txn and breaks
  Alembic's transaction control — that silently rolled DDL back during dev).
- **backend qualifies `world` explicitly.** Backend reads two schemas, so it uses
  explicit `{"schema": "world"}` on every read-only ORM class
  (`listings/models.py`) and `world.`-qualified raw SQL (`_NEIGHBOURS_SQL` in
  `listings/service.py`; the `gold_orphans` probe in `main.py`). Its Alembic env
  uses `include_name`/`include_object` filters so autogenerate manages only `app`
  and never touches `world.*`.
- **Migration revision IDs preserved** when porting 0001–0006 into ingestion —
  this lets an existing single-schema DB be relocated in place (`ALTER … SET
  SCHEMA world` + move `alembic_version`) without re-running DDL: the moved
  version row already equals ingestion's head.
- **`app` scope: boundary only.** The backend Alembic history restarts empty;
  users/sessions/bookmarks + the cross-schema FK ship in their feature PRs.
- **Migrations stay manual.** `docker compose up` does not auto-migrate (a broken
  migration can't block startup). Required order — bootstrap → ingestion
  `upgrade head` → (later) backend `upgrade head` — is enforced by the runbook
  (`agent-compound-docs/runbooks/schema-split-migration.md`), not compose.

## Shared models — Option B (read-models + drift test)

Ingestion uses raw SQL only and imports nothing from the backend, so there is no
ORM duplication to reconcile. The backend keeps **read-only** ORM classes pointed
at `world.*`; ingestion's migrations are the authoritative DDL. The contract is a
**drift test** (`services/backend/tests/integration/test_world_schema_drift.py`)
that reflects the **live `world` schema** and asserts every backend ORM table +
column exists in it — catching the "compiles in SQLAlchemy but Postgres rejects"
class of bug. This is the shared kernel's enforced boundary (see
`domain-context-map.md`).

Rejected: a shared models package (monorepo path dependency — unnecessary given
raw-SQL ingestion); separate databases (kills the cross-schema FK); soft FK
(cross-schema FKs work fine in one DB).

## Consequences

- The FK ordering (world before app) is load-bearing once `app.bookmarks` exists.
- `scripts/refresh-db.sh` carries both schemas automatically (full `pg_dump`); it
  now verifies `world` + `world.alembic_version` arrived, failing loudly on a
  partial/pre-split source.
- Tests: round-trip lives in ingestion now (`world.alembic_version`); the backend
  test fixture bootstraps + runs ingestion's Alembic to build `world.*` before
  the backend's own (no-op) `app` migrate.
- **Deferred for the bookmarks PR:** ON DELETE policy for
  `app.bookmarks.listing_id → world.listings.id` vs daily re-ingest (SET NULL +
  app cleanup, or a stable listing identity).
