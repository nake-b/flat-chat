# Plan — Schema & migration ownership split

**Status:** Planned, not started. Approved direction from PR #11 review (Q8).
**Owner:** backend + ingestion + postgres service.
**Goal:** Give each service ownership of the schema it writes, using **two Postgres schemas in one database**, with a hard cross-schema FK preserved. Service name stays **`ingestion`** (descriptive enough).

---

## 1. Motivation

Today the **backend** Alembic env owns **all** migrations — including iron/bronze/silver/gold/platinum tables that the **ingestion** service is the sole writer of. The backend defines those ORM classes (incl. `IronCard`/`RawListing`, which it never queries) only so the declarative mapper resolves `Listing`'s FK relationships and Alembic autogenerate sees the full schema (see the IronCard/RawListing docstrings, just clarified in PR #11).

We want: **each service owns the schema it is the source of truth for.**
- **ingestion** owns the medallion data (iron → platinum + geo-context tables).
- **backend** owns app data (users, sessions, bookmarks — currently TODO stubs).

The friction is "one database, two migration owners." This plan resolves it with two schemas + per-schema Alembic version tables + a clear bootstrap owner.

---

## 2. Decided model

**Two schemas, one database** (NOT two databases — we want the cross-schema FK):

| Schema | Tables | Owner |
|--------|--------|-------|
| `ingestion` | iron_cards, raw_listings, listings, listings_geo_context, listings_embeddings, listings_nearby_* (×6), transit_stops, transit_routes, transit_route_shapes, parks, schools, hospitals, social_monitoring, noise, green_volume, water_bodies, population_density, … | **ingestion** service |
| `app` | users, sessions, bookmarks (+ future app tables) | **backend** service |

**Cross-schema FK is valid and enforced:** `app.bookmarks.listing_id REFERENCES ingestion.listings(id)`. In Postgres, schemas are namespaces inside one DB; FKs/joins/views work across them. (The only hard wall is across *databases*.) So we keep referential integrity AND split ownership.

**Ownership of migrations:**
- **postgres service** (`services/postgres/`) owns the **bootstrap / "schema 0"**: `CREATE EXTENSION postgis; CREATE EXTENSION vector;`, `CREATE SCHEMA ingestion; CREATE SCHEMA app;`, roles/grants. Database-level infra neither app service should own.
- **ingestion** Alembic owns `ingestion.*` table migrations, tracked in `ingestion.alembic_version`.
- **backend** Alembic owns `app.*` table migrations, tracked in `app.alembic_version`.

**Migration ordering** (because of the cross-schema FK): bootstrap → ingestion `upgrade head` → backend `upgrade head`. `app.bookmarks` can't be created before `ingestion.listings` exists.

**Rejected alternatives** (capture in the decision doc):
- *Single-owner Alembic (one service migrates everything):* recouples both services' models into one env — exactly what we're splitting. ✗
- *Separate databases:* dodges migration ordering but **loses the cross-schema FK** (no referential integrity for bookmarks→listings; would need FDW/dblink). ✗
- *Soft reference (no FK on bookmarks):* unnecessary now that cross-schema FKs are confirmed to work; we keep the hard FK. ✗

---

## 3. The shared-model question (decide during Phase 4)

Backend still **reads** `ingestion.*` (SearchService, ListingService query listings/gold/junction tables). So it needs ORM classes for those tables even though ingestion owns their migrations. Options:

- **(A) Shared models package** — extract the medallion ORM definitions into a small package both services import. No drift, single source of truth. Cost: monorepo packaging (path dependency / internal package). **Recommended** if packaging is tolerable.
- **(B) Duplicated read-models + drift test** — backend keeps read-only ORM classes pointing at `ingestion.*`; ingestion owns the authoritative definitions + migrations; a CI test diffs the two (or diffs backend models against the live `ingestion` schema). Simpler packaging, needs the guard test.

Either way, **backend Alembic must NOT manage `ingestion.*`**: configure `env.py` with `include_schemas=True` + an `include_object`/`include_name` hook that excludes everything not in `app`. Symmetrically, ingestion's env excludes `app.*`.

---

## 4. Phases

### Phase 0 — Decision doc
Write `agent-compound-docs/decisions/schema-ownership-split.md`. Outline:
- **Context:** one DB, two writers; backend currently owns all migrations incl. tables it never writes.
- **Decision:** two schemas (`ingestion`, `app`), one DB; per-schema `alembic_version`; hard cross-schema FK `app.bookmarks → ingestion.listings`; postgres service owns bootstrap (extensions + schema creation + roles); each app service owns its schema's table migrations; ordering bootstrap → ingestion → backend.
- **Shared models:** record the A-vs-B choice and why.
- **Rejected:** single-owner alembic, separate databases, soft FK (with the cross-schema-FK fact that kills the soft-FK rationale).
- **Consequences:** migration ordering in compose; `search_path`/schema-qualified models; `refresh-db.sh` must dump/restore both schemas; round-trip test per service; the FK forces ingestion-first.
- Link from root `CLAUDE.md` decision-doc list + both service CLAUDE.md files.

### Phase 1 — Postgres bootstrap
- `services/postgres/` gains an init step (init SQL mounted at `/docker-entrypoint-initdb.d/`, or a tiny bootstrap migration owned here): `CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS vector; CREATE SCHEMA IF NOT EXISTS ingestion; CREATE SCHEMA IF NOT EXISTS app;` + roles/grants.
- Note: `docker-entrypoint-initdb.d` only runs on an **empty** data volume. For existing volumes, provide a one-shot idempotent bootstrap script teammates run once (and bake into `refresh-db.sh`).

### Phase 2 — ingestion owns its schema
- Create `services/ingestion/alembic/` (env + versions). Set `version_table="alembic_version"` **in schema `ingestion`** (`version_table_schema="ingestion"`), `include_schemas=True`, exclude non-`ingestion` objects.
- Move the medallion table definitions (currently only in backend `listings/models.py` + geo-context tables) to ingestion's authoritative models, all carrying `__table_args__ = {"schema": "ingestion"}` (or set via a shared `MetaData(schema="ingestion")`).
- Port the existing medallion migrations (0001–0006 portions that create medallion tables) into ingestion's history, rebased onto the bootstrapped schemas. **Keep schema vs data-fix migrations separate** (existing rule; 0004 geometry repairs stay irreversible — document).
- Round-trip test in ingestion: `upgrade head → downgrade base → upgrade head` against a throwaway DB.

### Phase 3 — backend owns app schema
- Reconfigure backend `alembic/env.py`: `version_table_schema="app"`, `include_schemas=True`, `include_object` excludes `ingestion.*` (so autogenerate never tries to create/drop ingestion tables).
- Remove the medallion *migrations* from backend (they move to ingestion). Backend migration history now starts the `app.*` tables.
- Create **new** `app.*` tables (these don't exist yet — they're the README TODO stubs): `users`, `sessions` (DB-backed session store — see `session-state-design.md`), `bookmarks` (`bookmarks_service.py` + `api/bookmarks.py` slot). `bookmarks.listing_id` → `ingestion.listings.id` (hard FK, ON DELETE depends on §soft-delete policy; with daily re-ingest, prefer a stable listing identity or ON DELETE SET NULL + app-side cleanup).
- Round-trip test in backend (app schema only).

### Phase 4 — backend read access to ingestion schema
- Decide A vs B (§3). Implement the chosen shared-model strategy.
- Backend read-models (`Listing`, `ListingGeoContext`, `ListingNearby*`, `ListingEmbedding`) point at `{"schema": "ingestion"}`.
- Backend no longer defines `IronCard`/`RawListing` at all (ingestion owns them; backend never reads them — the FK from `Listing` to them is ingestion-internal now). This finally removes the "dead" classes the PR #11 review flagged.
- Verify SearchService/ListingService queries still resolve with schema-qualified tables (asyncpg + SQLAlchemy emit `ingestion.listings`). Check the `near_lat/near_lon` PostGIS path and the junction `json_agg` query still run (integration tests against real PG).

### Phase 5 — orchestration + docs
- **Compose:** encode ordering — postgres healthy → ingestion migrate → backend migrate → backend serve. Either a dedicated one-shot `migrate` step per service or `depends_on` + entrypoint guards.
- **`scripts/refresh-db.sh`:** must `pg_dump`/restore **both** schemas (and recreate them on a fresh target). Verify the tailnet refresh still works end-to-end.
- **Docs sweep** (the keep-in-sync rule): root `CLAUDE.md` (Project Structure, Architecture Notes, migrations note), `services/backend/CLAUDE.md` (two-engines + tests + the new schema boundary), `services/ingestion/CLAUDE.md` (now owns migrations), both READMEs, `.env.example` if any new var, `docker-compose.yml`, `architecture.drawio` (+ `./render.sh`). Grep for stale "backend owns all migrations" statements.

---

## 5. Testing

- **Round-trip per service** (`test_alembic_round_trip.py` pattern, one per service): each schema migrates up/down cleanly in isolation against a throwaway DB. Keep schema/data-fix migrations separate so the cycle stays meaningful.
- **Cross-schema FK integration:** create a listing in `ingestion.listings`, a bookmark in `app.bookmarks` referencing it; assert the FK enforces (insert with bad listing_id fails; delete behavior matches policy).
- **Backend read-through:** existing SearchService/ListingService integration tests must pass with schema-qualified tables (run against real PG — this is the "compiles but PG rejects" guard for schema-qualification).
- **Drift test (if option B):** diff backend read-models against the live `ingestion` schema (or against ingestion's models).
- **Bootstrap idempotency:** run the bootstrap script twice; no error (IF NOT EXISTS everywhere).
- **`refresh-db.sh` smoke:** dump+restore round-trips both schemas + their version tables.

---

## 6. Risks / gotchas

- **Migration ordering is now load-bearing.** Backend migrate before ingestion migrate = FK target missing = failure. Bake the order into compose and document it loudly.
- **`docker-entrypoint-initdb.d` only runs on empty volumes.** Existing dev DBs won't auto-bootstrap; ship the idempotent one-shot + fold into `refresh-db.sh`.
- **Alembic autogenerate + schemas is finicky:** `include_schemas=True` is required or autogen ignores non-default schemas; the `include_object`/`include_name` filter must be correct or one env will try to drop the other's tables. Test autogen produces an empty diff after each phase.
- **`search_path`:** prefer explicit `{"schema": ...}` on models over relying on `search_path`, so behavior is deterministic regardless of connection role defaults.
- **`refresh-db.sh` / pg_dump:** ensure both schemas + both `alembic_version` tables are captured; a partial dump silently desyncs migration state.
- **Daily re-ingest vs bookmark FK:** if `listings` rows are deleted/replaced on re-ingest, bookmarks' FK target may vanish. Decide ON DELETE policy (SET NULL + app cleanup, or a stable listing identity that survives re-ingest). Capture in the decision doc.
- **PostGIS/pgvector in the right schema:** extensions are DB-global (usually in `public`); ensure types resolve from `ingestion.*` tables (qualify or keep extensions in `public` on the search_path).

## 7. Out of scope
- Service rename (`ingestion` stays).
- Splitting into separate databases (explicitly rejected — kills the FK).
- Building the actual users/auth flows (Phase 3 creates the *tables*; auth logic is separate work).
