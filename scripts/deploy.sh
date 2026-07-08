#!/usr/bin/env bash
#
# deploy.sh — the whole production deploy, idempotent and safe to re-run.
#
# Invoked by the self-hosted GitHub Actions runner (the `deploy` job in
# .github/workflows/ci.yml) on a green `main`, but also runnable by hand on the
# host. Because the deploy target IS this machine, there is nothing to SSH into —
# this is a plain local command run on the pre-existing repo + .env.
#
# What it does:
#   1. Sync the repo to origin/main (stashing any dirty tree first — never clobber
#      in-progress dev work; .env is gitignored so it is untouched).
#   2. Rebuild the serving + migration images (avoids the "stale baked image runs
#      old code/migrations after a pull" gotcha).
#   3. Run migrations in ownership order: world/ingestion, then app/backend.
#   4. Bring the prod stack up (base + host tailnet overlay + prod overlay).
#   5. Upsert accounts (rotates the dev credential to the .env password).
#   6. Health-gate: fail (non-zero exit) if the app never comes healthy, so the
#      GitHub job shows red on a bad deploy.
#
# See agent-compound-docs/decisions/deployment-guide.md.

set -euo pipefail

# Operate on the repo this script lives in (…/scripts/deploy.sh -> repo root),
# NOT on the runner's ephemeral checkout — that's where the prod .env lives.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The prod stack = base + host (tailnet DB) + prod overlay. Exported so every
# `docker compose` below sees the same file set without repeating -f flags.
export COMPOSE_FILE="docker-compose.yml:docker-compose.host.yml:docker-compose.prod.yml"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

# 1) Sync to origin/main, preserving any uncommitted work.
log "Syncing repo to origin/main"
if [ -n "$(git status --porcelain)" ]; then
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  log "Working tree dirty — stashing as deploy-autostash-$STAMP"
  git stash push --include-untracked -m "deploy-autostash-$STAMP"
fi
git fetch origin main
git checkout main
git reset --hard origin/main

# 2) Rebuild the images the deploy touches. backend + ingestion are the two that
# run migrations (a stale ingestion image is the classic "migrations 0008/0009
# aren't there" trap); frontend + nginx rebuild on the `up --build` below.
log "Building backend + ingestion images"
docker compose build backend ingestion

# 3) Migrations — world (ingestion-owned) first, then app (backend-owned).
log "Applying world (ingestion) migrations"
docker compose run --rm --no-deps -w /app/services/ingestion ingestion \
  uv run alembic upgrade head

log "Applying app (backend) migrations"
docker compose run --rm --no-deps -w /app/services/backend backend \
  uv run --no-dev alembic upgrade head

# 4) Bring the prod stack up. --remove-orphans clears any dev-only container
# (e.g. a leftover cloudflared from a prior run) that isn't in this file set.
log "Starting prod stack"
docker compose up -d --build --remove-orphans

# 5) Seed/rotate accounts (upsert — see scripts/seed_users.py). Runs against the
# now-migrated DB with the app's env (DATABASE_URL etc. from the container).
# The backend image contains only services/backend/, NOT the repo's top-level
# scripts/, so bind-mount it in for this one-off run. seed_users.py imports the
# installed flat_chat package from the image's venv; only the script file needs
# to be present.
log "Seeding / rotating accounts"
docker compose run --rm --no-deps -v "$REPO_ROOT/scripts:/app/scripts:ro" -w /app backend \
  uv run python scripts/seed_users.py

# 6) Health gate — retry a few times, then fail loudly if still unhealthy.
log "Health check"
for i in $(seq 1 12); do
  if curl -fsS http://localhost/api/health >/dev/null 2>&1; then
    log "Healthy. Deploy complete."
    exit 0
  fi
  echo "  not ready yet ($i/12)…"
  sleep 5
done

echo "ERROR: app did not become healthy after deploy" >&2
docker compose ps >&2
docker compose logs --tail 50 backend nginx >&2
exit 1
