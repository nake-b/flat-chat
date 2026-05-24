FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --no-dev
ENV PYTHONPATH=/app/src
# Default to running silver. Each tier can be invoked individually via:
#   docker compose run --rm ingestion python -m iron.loader <path>
#   docker compose run --rm ingestion python -m bronze.loader <path>
#   docker compose run --rm ingestion python -m silver.run
#   docker compose run --rm ingestion python -m silver.embed   # Jina backfill
CMD ["uv", "run", "python", "-m", "silver.run"]
