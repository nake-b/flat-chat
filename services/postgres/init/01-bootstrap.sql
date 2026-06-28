-- Database bootstrap — schema-ownership split (world / app).
--
-- Runs automatically ONCE on an empty data volume (Postgres executes every
-- file in /docker-entrypoint-initdb.d/ in lexical order on first init). For an
-- EXISTING volume this does NOT re-run — use scripts/bootstrap-schemas.sh, which
-- applies the same statements idempotently.
--
-- See agent-compound-docs/decisions/schema-ownership-split.md.

-- Extensions are DB-global. Keep them in `public` (on every role's search_path)
-- so geometry/vector column types resolve for world.* tables regardless of the
-- connection's search_path.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
-- pg_trgm powers the trigram (`%`) name search behind the `world.named_places`
-- gazetteer view (locate_place). The per-table GIN trgm indexes are created by
-- the ingestion 0007 migration; the extension itself is DB-global, so it lives
-- here in `public` alongside postgis/vector.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Two ownership schemas:
--   world — reference data the ingestion service is the source of truth for
--           (medallion iron→platinum + geo-context). Migrated by ingestion's
--           Alembic; tracked in world.alembic_version.
--   app   — product state the backend owns (users/sessions/bookmarks, future).
--           Migrated by backend's Alembic; tracked in app.alembic_version.
CREATE SCHEMA IF NOT EXISTS world;
CREATE SCHEMA IF NOT EXISTS app;
