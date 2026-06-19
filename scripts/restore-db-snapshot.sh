#!/usr/bin/env bash
# Restore the Postgres data volume from a shared snapshot tarball.
#
# WIPES the local `postgres_data` volume and replaces it with the snapshot's
# contents. Use this to load the shared listings DB on a fresh clone — no
# scrapers, no ETL. Get the snapshot file from the organizers (see HACKATHON.md).
#
# Usage:
#   ./scripts/restore-db-snapshot.sh [snapshot.tgz]   # default: flat-chat-db-snapshot.tgz
#   POSTGRES_VOLUME=foo_postgres_data ./scripts/restore-db-snapshot.sh path/to/snap.tgz

set -euo pipefail

SNAP="${1:-flat-chat-db-snapshot.tgz}"

if [ ! -f "$SNAP" ]; then
  echo "Error: snapshot '$SNAP' not found." >&2
  echo "       Pass its path: ./scripts/restore-db-snapshot.sh /path/to/snapshot.tgz" >&2
  exit 1
fi

# The volume name compose will use for this checkout (= <project>_postgres_data,
# project defaults to the lowercased directory name). Creating it now with that
# exact name means `docker compose up` later mounts the restored data.
proj="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
VOLUME="${POSTGRES_VOLUME:-${proj}_postgres_data}"

SNAP_DIR="$(cd "$(dirname "$SNAP")" && pwd)"
SNAP_FILE="$(basename "$SNAP")"

echo "→ Snapshot: $SNAP_DIR/$SNAP_FILE"
echo "→ Volume:   $VOLUME"
echo

read -r -p "This WIPES volume '$VOLUME' and replaces it with the snapshot. Continue? [y/N] " ans
case "$ans" in
  y | Y) ;;
  *) echo "Aborted."; exit 1 ;;
esac

# Bring the stack down so nothing holds the volume open. `down` (no -v) keeps
# volumes; we clear + repopulate the target volume explicitly below.
docker compose down >/dev/null 2>&1 || true

docker volume create "$VOLUME" >/dev/null

docker run --rm \
  -v "$VOLUME":/data \
  -v "$SNAP_DIR":/backup:ro \
  alpine sh -c "find /data -mindepth 1 -delete; tar xzf '/backup/$SNAP_FILE' -C /data"

echo
echo "✓ Restored into '$VOLUME'. Bring the stack up:  docker compose up"
