-- Relocate an EXISTING single-schema (public) flat-chat DB into the two-schema
-- (world / app) layout — in place, without re-creating tables or losing data.
--
-- Use this on DBs whose data must survive: the canonical tailnet DB
-- (flat-chat-db) and any teammate's local DB with precious local-only rows.
-- Fresh/disposable DBs don't need it — they bootstrap empty schemas and
-- restore from the already-migrated canonical dump (see the runbook).
--
-- PRECONDITIONS:
--   * The medallion tables currently live in `public` (pre-split).
--   * Migration revision IDs were preserved when porting to ingestion, so the
--     moved `alembic_version` row already equals ingestion's head — no re-run.
--
-- IDEMPOTENT-ISH: every statement is `IF EXISTS`, so re-running after a
-- successful move is a no-op (the tables are already out of `public`).
--
-- AFTER running this: nothing else (boundary-only) — backend has no `app`
-- revisions yet; ingestion's version row moved with the tables and equals head.
--
-- BACK UP FIRST on the canonical DB:  pg_dump ... > backup.sql
--
-- See agent-compound-docs/decisions/schema-ownership-split.md and
-- agent-compound-docs/runbooks/schema-split-migration.md.

BEGIN;

CREATE SCHEMA IF NOT EXISTS world;
CREATE SCHEMA IF NOT EXISTS app;

-- Medallion: iron / bronze / silver
ALTER TABLE IF EXISTS public.iron_cards            SET SCHEMA world;
ALTER TABLE IF EXISTS public.raw_listings          SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings              SET SCHEMA world;

-- Geo-context silver (Berlin GDI WFS + VBB GTFS)
ALTER TABLE IF EXISTS public.schools               SET SCHEMA world;
ALTER TABLE IF EXISTS public.school_catchments     SET SCHEMA world;
ALTER TABLE IF EXISTS public.population_density_2025 SET SCHEMA world;
ALTER TABLE IF EXISTS public.street_noise_2022     SET SCHEMA world;
ALTER TABLE IF EXISTS public.green_volume_2020     SET SCHEMA world;
ALTER TABLE IF EXISTS public.parks                 SET SCHEMA world;
ALTER TABLE IF EXISTS public.playgrounds           SET SCHEMA world;
ALTER TABLE IF EXISTS public.hospitals             SET SCHEMA world;
ALTER TABLE IF EXISTS public.disabled_parking      SET SCHEMA world;
ALTER TABLE IF EXISTS public.social_monitoring_2025 SET SCHEMA world;
ALTER TABLE IF EXISTS public.water_bodies          SET SCHEMA world;
ALTER TABLE IF EXISTS public.transit_stops         SET SCHEMA world;
ALTER TABLE IF EXISTS public.transit_routes        SET SCHEMA world;
ALTER TABLE IF EXISTS public.transit_route_shapes  SET SCHEMA world;

-- Gold + platinum
ALTER TABLE IF EXISTS public.listings_geo_context  SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_embeddings   SET SCHEMA world;

-- POI junction tables
ALTER TABLE IF EXISTS public.listings_nearby_transit     SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_nearby_schools     SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_nearby_hospitals   SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_nearby_parks       SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_nearby_playgrounds SET SCHEMA world;
ALTER TABLE IF EXISTS public.listings_nearby_water       SET SCHEMA world;

-- Alembic state: the existing public.alembic_version tracks the medallion
-- history, now owned by ingestion. Move it so ingestion sees itself at head.
ALTER TABLE IF EXISTS public.alembic_version       SET SCHEMA world;

-- NOTE on the listings updated_at trigger function: `listings_set_updated_at()`
-- stays in `public`. The trigger on world.listings resolves it via the
-- ingestion connection's search_path (`world, public`). No move needed; FK
-- constraints are rewritten automatically by SET SCHEMA within this txn.

COMMIT;
