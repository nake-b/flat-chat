# Runbook — migrating to the `world` / `app` schema split

This change moves all medallion + geo-context tables into a `world` Postgres
schema (owned by ingestion) and reserves an `app` schema (owned by the backend).
It spans postgres + ingestion + backend + dev tooling, so follow the section for
**your** role. Decision record: `agent-compound-docs/decisions/schema-ownership-split.md`.

There are two ways to reach the two-schema layout:

- **Relocate in place** — for DBs whose data must survive (the canonical tailnet
  DB, or a teammate's local DB with precious rows). Runs
  `scripts/relocate-to-world.sql`.
- **Fresh + refresh** — for disposable local DBs. Bootstrap builds empty schemas;
  data comes from the already-migrated canonical via `scripts/refresh-db.sh`.

---

## Canonical host (run ONCE, by whoever serves `flat-chat-db`)

The canonical DB has irreplaceable scraped data — **relocate in place, never nuke.**

```bash
# 1. Back up first (data is irreplaceable).
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  > backup-pre-split-$(date +%F).sql

# 2. Relocate: move all medallion tables + alembic_version into `world`,
#    create the empty `app` schema. Transactional, idempotent-ish.
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -f - < scripts/relocate-to-world.sql

# 3. Nothing else to run (boundary-only): ingestion's version row moved with the
#    tables and equals head; backend has no `app` revisions yet.

# 4. Re-serve via the Tailscale overlay (host loads docker-compose.host.yml).
docker compose up -d
```

Then **announce to the team: "canonical is now two-schema — pull `main`, reset and
refresh."** Order matters: the canonical must be relocated *before* anyone refreshes
from it, or a teammate pulls old-shaped data into a new-shaped stack.

Verify:
```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dn"     # world, app present
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT to_regclass('world.listings'), to_regclass('world.alembic_version');"
```

---

## Every teammate (the easy path)

Your local DB is disposable — nuke it and refresh from the already-migrated canonical:

```bash
git pull                       # get the schema-split merge
docker compose down -v         # drop the local volume
docker compose up -d           # postgres init creates extensions + world/app schemas
./scripts/refresh-db.sh        # restore from canonical (dump carries world.* + app.*)
```

`refresh-db.sh` now verifies the `world` schema + `world.alembic_version` arrived
and fails loudly otherwise. If you have precious local-only data, run the
relocate path instead (below) before refreshing.

---

## Implementer / preserving a local DB (relocate path)

```bash
# Ensure the schemas/extensions exist (existing volume → init SQL won't re-run).
./scripts/bootstrap-schemas.sh
# Move existing public tables into world (keeps your data).
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -f - < scripts/relocate-to-world.sql
```

---

## Manual migrations (required order)

`docker compose up` does NOT auto-migrate. On a fresh/bootstrapped DB:

```bash
# 1. ingestion owns world.*  (bootstrap must have created the schemas first)
docker compose run --rm ingestion uv run alembic upgrade head
# 2. backend owns app.*  — no-op today (boundary-only; empty history)
docker compose run --rm backend uv run alembic upgrade head
```

> ⚠️ The order is load-bearing **once `app.bookmarks` exists** (its FK targets
> `world.listings`). Until then there is only one thing to run.

---

## Tests

```bash
docker compose exec postgres createdb -U flat_chat flat_chat_test
export TEST_DATABASE_URL=postgresql://flat_chat:flat_chat@localhost:5432/flat_chat_test

# ingestion: world-schema round-trip
cd services/ingestion && uv run pytest tests/integration/test_alembic_round_trip.py

# backend: drift guard + read-through (fixture bootstraps + runs ingestion's alembic)
cd services/backend && uv run pytest
```

## Rollback

Restore the pre-split `pg_dump` backup into a fresh DB. The split is a structural
move (tables relocated, not transformed), so the backup is a faithful pre-split
snapshot. `git revert` the merge to restore single-schema code.
