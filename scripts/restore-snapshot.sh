#!/usr/bin/env bash
# Restore a raw PGDATA snapshot (postgres_data.tar.gz) into the local
# `postgres_data` Docker volume used by this project's docker compose stack.
#
# The tarball must be a snapshot of the postgres data directory (contains
# `postgresql.auto.conf`, `base/`, `pg_wal/`, etc.), NOT a pg_dump SQL file.
# For SQL dumps, use scripts/refresh-db.sh instead.
#
# WIPES the current local postgres volume. Local-only data is lost.
#
# Usage:
#   ./scripts/restore-snapshot.sh                          # uses ./postgres_data.tar.gz
#   ./scripts/restore-snapshot.sh path/to/snapshot.tar.gz
#   ./scripts/restore-snapshot.sh -y snapshot.tar.gz       # skip confirmation
#
# Requirements:
#   - Docker + docker compose available
#   - You're sitting in the repo root (so `docker compose` finds the right project)
#   - The tarball's Postgres major version matches services/postgres/Dockerfile (pg16)

set -euo pipefail

ASSUME_YES=0
TARBALL="./postgres_data.tar.gz"

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    -*)
      echo "Unknown flag: $1" >&2; exit 2 ;;
    *)
      TARBALL="$1"; shift ;;
  esac
done

if [ ! -f "$TARBALL" ]; then
  echo "Error: tarball not found at '$TARBALL'." >&2
  echo "       Pass the path as an argument, or place the file at ./postgres_data.tar.gz." >&2
  exit 1
fi

# Sanity check: this should be a PGDATA snapshot, not a pg_dump.
if ! tar -tzf "$TARBALL" 2>/dev/null | grep -q 'postgresql\.auto\.conf\|^./PG_VERSION\|^PG_VERSION'; then
  echo "Error: '$TARBALL' doesn't look like a raw PGDATA snapshot" >&2
  echo "       (no postgresql.auto.conf / PG_VERSION entry found)." >&2
  echo "       If this is a pg_dump SQL file, use scripts/refresh-db.sh instead." >&2
  exit 1
fi

# Resolve compose project + volume name. `docker compose config` prints the
# fully-qualified volume name (e.g. flat-chat_postgres_data) so we don't have
# to guess the project prefix.
VOLUME_NAME=$(
  docker compose config --format json 2>/dev/null \
    | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
vols = cfg.get("volumes", {}) or {}
v = vols.get("postgres_data") or {}
name = v.get("name")
if not name:
    sys.exit("no postgres_data volume in compose config")
print(name)
'
) || {
  echo "Error: couldn't resolve the postgres_data volume name via 'docker compose config'." >&2
  echo "       Are you in the repo root with docker compose available?" >&2
  exit 1
}

DATA_PATH="/var/lib/postgresql/data"

echo "→ Snapshot: $TARBALL"
echo "→ Volume:   $VOLUME_NAME (mounted at $DATA_PATH)"
echo

if [ "$ASSUME_YES" -ne 1 ]; then
  printf "This will WIPE the current contents of '%s' and replace them with the snapshot.\n" "$VOLUME_NAME"
  printf "Continue? [y/N] "
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

echo "→ Stopping postgres service…"
docker compose stop postgres >/dev/null

echo "→ Wiping volume and extracting snapshot…"
docker run --rm \
  -v "$VOLUME_NAME:$DATA_PATH" \
  -v "$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL"):/snapshot.tar.gz:ro" \
  --entrypoint sh \
  postgres:16 \
  -c "
    set -e
    find $DATA_PATH -mindepth 1 -delete
    tar -xzf /snapshot.tar.gz -C $DATA_PATH
    chown -R postgres:postgres $DATA_PATH
    chmod 700 $DATA_PATH
  "

echo "→ Starting postgres service…"
docker compose start postgres >/dev/null

echo
echo "✓ Snapshot restored into $VOLUME_NAME."
echo "  Tail logs to confirm startup:"
echo "    docker compose logs -f postgres"
