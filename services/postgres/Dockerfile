FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgis postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

# Bootstrap extensions + ownership schemas (world / app) on first init.
# Runs ONLY on an empty data volume; existing volumes use
# scripts/bootstrap-schemas.sh. See schema-ownership-split.md.
COPY init/ /docker-entrypoint-initdb.d/

