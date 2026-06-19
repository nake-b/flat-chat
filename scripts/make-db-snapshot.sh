#!/usr/bin/env bash
# Create a shareable raw snapshot of the Postgres data volume.
#
# Produces a bit-for-bit tarball of the `postgres_data` volume (~600 MB) that
# other people restore with `restore-db-snapshot.sh`. Safe to share because
# everyone runs the identical postgres image (services/postgres/Dockerfile —
# pgvector + PostGIS), so the binary data dir is portable.
#
# Usage:
#   ./scripts/make-db-snapshot.sh [output.tgz]      # default: flat-chat-db-snapshot.tgz
#   POSTGRES_VOLUME=foo_postgres_data ./scripts/make-db-snapshot.sh

set -euo pipefail

OUT="${1:-flat-chat-db-snapshot.tgz}"

# Resolve the actual volume backing the postgres data dir. Prefer inspecting the
# live container (exact); fall back to the compose-computed `<project>_postgres_data`.
resolve_volume() {
  local cid name proj
  cid="$(docker compose ps -aq postgres 2>/dev/null | head -1 || true)"
  if [ -n "$cid" ]; then
    name="$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/var/lib/postgresql/data" }}{{ .Name }}{{ end }}{{ end }}' "$cid" 2>/dev/null || true)"
    [ -n "$name" ] && { echo "$name"; return; }
  fi
  proj="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
  echo "${proj}_postgres_data"
}

VOLUME="${POSTGRES_VOLUME:-$(resolve_volume)}"

if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  echo "Error: volume '$VOLUME' not found. Start the stack once (docker compose up)" >&2
  echo "       or set POSTGRES_VOLUME explicitly." >&2
  exit 1
fi

echo "→ Volume: $VOLUME"
echo "→ Output: $OUT"

# Stop postgres for a consistent on-disk copy — avoids capturing torn pages or
# half-written WAL while the server is mid-checkpoint.
echo "→ Stopping postgres for a consistent copy…"
docker compose stop postgres >/dev/null 2>&1 || true

docker run --rm \
  -v "$VOLUME":/data:ro \
  -v "$PWD":/backup \
  alpine tar czf "/backup/$OUT" -C /data .

docker compose start postgres >/dev/null 2>&1 || true

echo "✓ Snapshot written to $OUT ($(du -h "$OUT" | cut -f1)). Share it; restore with scripts/restore-db-snapshot.sh."
