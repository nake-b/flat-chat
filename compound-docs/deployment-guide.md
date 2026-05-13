# Deployment Guide

## Local Development

### With Docker (recommended)

```bash
# Create .env from template (first time only)
cp .env.example .env

# Start all services
docker compose up --build

# Open the app
open http://localhost
```

This starts Nginx (port 80), frontend, backend, and PostgreSQL. The frontend is at `http://localhost`, the API at `http://localhost/api/health`.

### Without Docker

**Backend:**
```bash
cd services/backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd services/frontend
npm install
npm run dev
```

Vite dev server runs on `http://localhost:5173` and proxies `/api/*` to `http://localhost:8000`.

### Running Ingestion

See **[services/ingestion/README.md](../services/ingestion/README.md)** for the full pipeline — three tiers (iron → bronze → silver), the scrape commands, the JSON-replay path, and the cursor-resume semantics.

Headline commands once first-time setup is done:

```bash
# Apply migrations once
docker compose run --rm backend uv run alembic upgrade head

# Scrape (per source) — VPN on, no DATABASE_URL prefix needed (auto-loaded from .env)
cd services/ingestion/src/scraper/<source> && npm run scrape:cards
cd services/ingestion/src/scraper/<source> && npm run scrape:details

# Normalize bronze → silver
cd services/ingestion && PYTHONPATH=src python3 -m silver.run
```

## Production (VPS)

### One-Time Setup

1. SSH into VPS
2. Install Docker and Docker Compose
3. Clone the repository
4. Create `.env` from `.env.example` (set a real `POSTGRES_PASSWORD`)
5. Add crontab entry for ingestion:
   ```bash
   crontab -e
   # Add: 0 3 * * * cd /path/to/flat-chat && docker compose --profile ingestion run --rm ingestion >> /var/log/ingestion.log 2>&1
   ```

### Deploying Updates

```bash
ssh your-vps "cd /path/to/flat-chat && git pull && docker compose up -d --build"
```

### Service Architecture in Production

```
Internet → Cloudflare → VPS:80 → [Nginx] ─┬─ / → [Frontend]
                                            └─ /api/ → [Backend] → [PostgreSQL]
```

Only Nginx exposes a port. All other services are internal to the Docker network.
