# Build context is the REPO ROOT (see docker-compose.yml `context: .`), not this
# service dir — the image must see the workspace root `uv.lock` to install the
# exact, CI-tested dependency graph. `uv sync --frozen` then FAILS loudly if
# pyproject.toml and uv.lock disagree, instead of silently re-resolving the way
# a service-dir build would. See agent-compound-docs/decisions/pydantic-v2-migration.md.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Workspace metadata only first → the heavy dependency layer caches and is
# rebuilt only when a pyproject or the lock changes, not on every source edit.
# uv needs every member's pyproject present to validate the workspace graph.
COPY pyproject.toml uv.lock ./
COPY services/backend/pyproject.toml services/backend/
COPY services/ingestion/pyproject.toml services/ingestion/
RUN uv sync --frozen --no-dev --no-install-workspace --package flat-chat-ingestion

# Now the source; the second sync just links the editable package (fast).
COPY services/ingestion/ services/ingestion/
RUN uv sync --frozen --no-dev --package flat-chat-ingestion

# Medallion modules (silver, gold, bronze, geo_context, …) are imported as
# top-level modules off PYTHONPATH, not via the installed wheel.
ENV PYTHONPATH=/app/services/ingestion/src
# WORKDIR is the workspace root, so `uv run` here would otherwise try to
# re-sync the *whole* workspace (including the backend member, whose source
# isn't in this image) and fail. The env is already synced above, so skip it.
ENV UV_NO_SYNC=1

# Default to running silver. Each tier can be invoked individually via:
#   docker compose run --rm ingestion python -m iron.loader <path>
#   docker compose run --rm ingestion python -m bronze.loader <path>
#   docker compose run --rm ingestion python -m silver.run
#   docker compose run --rm ingestion python -m silver.embed   # Jina backfill
CMD ["uv", "run", "python", "-m", "silver.run"]
