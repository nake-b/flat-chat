#!/usr/bin/env bash
# Idempotent bootstrap for EXISTING Postgres volumes (the init SQL in
# services/postgres/init/ only runs on a fresh volume). Creates the postgis /
# vector extensions in `public` and the `world` + `app` schemas.
#
# Safe to run repeatedly — every statement is IF NOT EXISTS.
#
# Usage (against the local dev container):
#   ./scripts/bootstrap-schemas.sh
#
# This does NOT move existing tables into `world` — for that, see
# scripts/relocate-to-world.sql. Bootstrap just guarantees the schemas exist.

set -euo pipefail

if [ -f .env ]; then
  set -a; source .env; set +a
fi
DB_USER="${POSTGRES_USER:-flat_chat}"
DB_NAME="${POSTGRES_DB:-flat_chat}"

echo "→ Bootstrapping extensions + schemas on local postgres ($DB_NAME)"

docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS world;
CREATE SCHEMA IF NOT EXISTS app;
SQL

echo "✓ Extensions + world/app schemas present."
