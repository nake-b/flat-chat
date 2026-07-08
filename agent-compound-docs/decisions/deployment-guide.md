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
uv run uvicorn flat_chat.main:app --host 0.0.0.0 --port 8000 --reload
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

## Production (self-hosted via Cloudflare Tunnel + CI/CD)

The deploy target is a single self-hosted machine (for the current demo, the same
PC that hosts the tailnet DB). Two design choices define it:

- **Exposure = Cloudflare Tunnel.** `cloudflared` runs as a compose service and
  makes an OUTBOUND connection to Cloudflare's edge; public visitors hit
  Cloudflare, which relays requests down the tunnel to `nginx:80`. No inbound
  port-forwarding, no public IP, works behind NAT/CGNAT/VPN. Cloudflare terminates
  HTTPS (free, auto-renewed) and hides the host IP.
  - *Rejected: a Hetzner (or any) VPS* — unnecessary cost/ops for a university
    demo.
  - *Rejected: a VPN static IP (Surfshark)* — it's a static **exit** IP with no
    inbound port-forwarding, so nothing on the internet can reach into the host.
    A static exit IP alone cannot host anything.
- **CD = a self-hosted GitHub Actions runner on the host.** On a green `main`, the
  `deploy` job (in `.github/workflows/ci.yml`) runs `scripts/deploy.sh` locally on
  the host — no SSH, because the host *is* the runner. CI test jobs stay on
  GitHub-hosted runners, so untrusted PR code never executes on the host; the
  `deploy` job is gated to `push` on `main` only.

### Service Architecture in Production

```
Browser ──HTTPS──▶ Cloudflare edge ──tunnel──▶ [cloudflared] ──▶ [Nginx] ─┬─ / → [Frontend]
                                                (outbound only)            └─ /api/ → [Backend] → [PostgreSQL]
```

Nginx is published on `127.0.0.1:80` only (the public path is the tunnel); every
other service is internal. Postgres stays loopback/tailnet-only — never on the
public path.

### One-Time Setup

1. **Domain + tunnel.** Buy a domain, add the zone to Cloudflare (nameservers).
   In Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared)**,
   copy the token into the host `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, and add a
   public hostname mapping `app.<domain> → http://nginx:80`.
2. **Prod secrets** in the host `.env`: a fresh `JWT_SECRET`
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) and a strong
   `DEV_USER_PASSWORD` (the seed *upserts* it, so it rotates the default `dev`
   login). `COOKIE_SECURE=true` is applied by the prod overlay automatically.
3. **Self-hosted runner.** Repo → **Settings → Actions → Runners → New
   self-hosted runner (Linux x64)**; configure it with the label `flatchat-prod`,
   then install it as a service so it survives reboots:
   `sudo ./svc.sh install && sudo ./svc.sh start`. Ensure the runner's user is in
   the `docker` group.
4. **Ingestion cron** (optional): `0 3 * * * cd /path/to/flat-chat && docker compose --profile ingestion run --rm ingestion >> /var/log/ingestion.log 2>&1`

### Deploying Updates

Automatic: push to `main` → CI (lint/type/test) → on green, the self-hosted runner
runs `scripts/deploy.sh`, which syncs to `origin/main`, rebuilds the serving +
migration images, applies world→app migrations, brings up the prod stack
(`docker-compose.yml:docker-compose.host.yml:docker-compose.prod.yml`), upserts
accounts, and health-gates. To deploy by hand, run `scripts/deploy.sh` on the host.
