#!/usr/bin/env bash
# One-time data prep for the travel-time routing engines (the `routing` compose
# profile). Builds the OSRM car graph and the MOTIS transit+street graph into
# ./data/routing/{osrm,motis}, which docker-compose.yml bind-mounts.
#
# Re-run to refresh the Berlin OSM extract / VBB GTFS feed (VBB republishes
# ~twice weekly; a stale feed yields zero trips for future dates). Idempotent —
# re-downloads inputs and rebuilds graphs in place.
#
# Usage:  ./scripts/prep-routing.sh
# Then:   docker compose --profile routing up -d osrm motis
#
# See agent-compound-docs/decisions/travel-time-routing.md.
set -euo pipefail

OSM_URL="https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf"
# VBB GTFS — same feed the ingestion geo-context pipeline uses
# (services/ingestion/src/geo_context/extract/gtfs.py).
GTFS_URL="https://www.vbb.de/vbbgtfs"

OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
MOTIS_IMAGE="ghcr.io/motis-project/motis:latest"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OSRM_DIR="$ROOT/data/routing/osrm"
MOTIS_DIR="$ROOT/data/routing/motis"
PBF="berlin-latest.osm.pbf"

mkdir -p "$OSRM_DIR" "$MOTIS_DIR"

echo "==> Downloading Berlin OSM extract"
curl -fsSL -o "$OSRM_DIR/$PBF" "$OSM_URL"
cp "$OSRM_DIR/$PBF" "$MOTIS_DIR/$PBF"

echo "==> Downloading VBB GTFS feed"
curl -fsSL -o "$MOTIS_DIR/gtfs.zip" "$GTFS_URL"

echo "==> OSRM: extract → partition → customize (car profile, MLD)"
docker run --rm -v "$OSRM_DIR:/data" "$OSRM_IMAGE" \
  osrm-extract -p /opt/car.lua "/data/$PBF"
docker run --rm -v "$OSRM_DIR:/data" "$OSRM_IMAGE" \
  osrm-partition "/data/berlin-latest.osrm"
docker run --rm -v "$OSRM_DIR:/data" "$OSRM_IMAGE" \
  osrm-customize "/data/berlin-latest.osrm"

echo "==> MOTIS: writing config.yml (street routing + VBB timetable)"
cat > "$MOTIS_DIR/config.yml" <<YAML
osm: $PBF
street_routing: true
geocoding: false
reverse_geocoding: false
timetable:
  first_day: TODAY
  num_days: 365
  datasets:
    vbb:
      path: gtfs.zip
YAML

echo "==> MOTIS: import"
rm -rf "$MOTIS_DIR/data"
docker run --rm -v "$MOTIS_DIR:/work" -w /work "$MOTIS_IMAGE" /motis import

# Re-running this script refreshes the loaded timetable window: `first_day:
# TODAY` starts it at today and the freshly-downloaded VBB feed extends the
# horizon (the feed carries a short service calendar, so the window needs
# regular re-import to stay current). The backend reads the loaded window from
# MOTIS `/metrics` and clamps + labels transit departures accordingly (see
# agent-compound-docs/decisions/travel-time-routing.md §Freshness).
echo "==> Restarting the routing engines to load the fresh graphs"
if docker compose --profile routing up -d --no-deps --force-recreate osrm motis; then
  echo "==> Done. Verify the loaded transit feed window:"
  echo "    curl -s localhost:18080/metrics | grep nigiri_timetable_.*_day"
else
  echo "==> Import done, but the compose restart failed — start manually:"
  echo "    docker compose --profile routing up -d --force-recreate osrm motis"
fi
