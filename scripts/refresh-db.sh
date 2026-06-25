#!/usr/bin/env bash
# Refresh your local Postgres from the team's shared tailnet DB (flat-chat-db).
#
# - Requires Tailscale client running on this host and joined to the team's tailnet.
# - Requires your local stack to be up (docker compose up) so the local postgres
#   container exists to restore into.
# - WIPES + RELOADS the local DB. Local-only data is lost.
#
# Usage:
#   ./scripts/refresh-db.sh
#
# Override the remote (e.g. raw IP if MagicDNS isn't working):
#   FLAT_CHAT_DB_HOST=100.64.0.5 ./scripts/refresh-db.sh

set -euo pipefail

# Locate the tailnet device. Prefer explicit env override; else find by hostname.
REMOTE_HOST="${FLAT_CHAT_DB_HOST:-}"
if [ -z "$REMOTE_HOST" ]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "Error: tailscale CLI not found. Install Tailscale (https://tailscale.com/download) or set FLAT_CHAT_DB_HOST." >&2
    exit 1
  fi
  REMOTE_HOST=$(tailscale status 2>/dev/null | awk '/flat-chat-db/ {print $1; exit}' || true)
  if [ -z "$REMOTE_HOST" ]; then
    echo "Error: couldn't find flat-chat-db on the tailnet." >&2
    echo "  Check: tailscale status" >&2
    echo "  Or set FLAT_CHAT_DB_HOST to the host's tailnet IP." >&2
    exit 1
  fi
fi

# Pull DB creds from .env if present; fall back to defaults.
if [ -f .env ]; then
  set -a; source .env; set +a
fi
DB_USER="${POSTGRES_USER:-flat_chat}"
DB_PASS="${POSTGRES_PASSWORD:-flat_chat}"
DB_NAME="${POSTGRES_DB:-flat_chat}"

echo "→ Source:      $REMOTE_HOST"
echo "→ Destination: local postgres container ($DB_NAME)"
echo

# Terminate other connections so we can drop & recreate the local DB cleanly.
docker compose exec -T postgres psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DB_NAME;
CREATE DATABASE $DB_NAME;
SQL

# Stream pg_dump from tailnet → local psql. pg_dump runs in a one-shot postgres
# container so teammates don't need pg_dump installed locally.
docker run --rm -i \
  -e PGPASSWORD="$DB_PASS" \
  postgres:16 \
  pg_dump -h "$REMOTE_HOST" -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-acl --format=plain \
| docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 --quiet

# --- Post-restore sanity: the two-schema layout must have come across whole.
# A plain pg_dump carries CREATE SCHEMA + both alembic_version tables; a partial
# dump (or refreshing from a not-yet-migrated canonical) silently desyncs
# migration state, so fail loudly here instead of at first query.
echo
echo "→ Verifying world/app schema layout…"
MISSING=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "
  SELECT string_agg(missing, ', ') FROM (
    SELECT 'schema world'            AS missing WHERE to_regnamespace('world') IS NULL
    UNION ALL
    SELECT 'world.listings'          WHERE to_regclass('world.listings') IS NULL
    UNION ALL
    SELECT 'world.alembic_version'   WHERE to_regclass('world.alembic_version') IS NULL
  ) t;
" | tr -d '[:space:]')

if [ -n "$MISSING" ]; then
  echo "✗ Refresh incomplete — missing: ${MISSING}" >&2
  echo "  The source DB may predate the schema-ownership split, or the dump was partial." >&2
  echo "  See agent-compound-docs/runbooks/schema-split-migration.md." >&2
  exit 1
fi

echo
echo "✓ Local DB refreshed from $REMOTE_HOST (world/app schemas verified)."
