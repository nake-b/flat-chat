# Shared dev database

## Problem

The team needs consistent dev data across machines. The prior workflow — exporting the Postgres container as a tarball and shipping it through random fileshares — is slow, lossy, and there's no source of truth for "what's the current dataset everyone should be on". But teammates also want fast, offline-friendly local dev, including the ability to mutate the DB freely while testing.

## Decision

**Local-first with a tailnet-hosted canonical seed.**

- Every teammate runs the full base `docker-compose.yml` stack on their own machine, including their own Postgres on the docker bridge. Daily dev hits the local DB — no network round-trips, no shared-state hazards from another teammate's session.
- The **canonical seed** lives on the host machine's Postgres, exposed to the team's tailnet by a Tailscale sidecar registered as hostname `flat-chat-db`. Only the host loads the `docker-compose.host.yml` overlay (via `COMPOSE_FILE` in `.env`), which wraps the host's already-running postgres with `network_mode: "service:tailscale"`. Teammates never load the overlay, so they never spin up their own tailnet device or collide on the `flat-chat-db` name.
- **Refresh on demand** via `./scripts/refresh-db.sh` (committed in `scripts/`). The script auto-discovers the host's tailnet IP via `tailscale status | grep flat-chat-db`, drops + recreates the teammate's local DB, and streams `pg_dump` over the tailnet into the local postgres container. `pg_dump` runs in a one-shot `postgres:16` container so teammates don't need it installed natively.

Practical effect:
- Cold-start onboarding for a teammate: install Tailscale client, join the tailnet, `git clone`, `cp .env.example .env`, fill in API keys, `docker compose up`, `./scripts/refresh-db.sh`. Done — full stack running locally against the team's current data.
- Day-to-day dev: just `docker compose up` and work against your local DB. Re-run the refresh script whenever you want fresh data.
- Host setup: see "Setup (one-time, host)" below.

## Rejected alternatives

- **Azure Database for PostgreSQL Flexible Server (~$12–15/month).** Storage is free at our scale (~30MB); the cost is a 24/7 1-vCPU VM that managed Postgres can't scale to zero on. Overkill for "share a seed dataset".
- **Azure Blob Storage + `make seed` (~$0/month).** Same shape as our refresh script, but the dump goes through an extra hop (host → blob → teammate) and someone has to remember to upload after every ingestion run. Direct tailnet pull keeps the host as the single source of truth automatically.
- **Neon free tier (serverless Postgres, $0).** Would work technically, but lives outside the Azure RG and adds another vendor to the trust boundary.
- **Teammates point their backend at the host DB directly (no local Postgres).** Considered and rejected: kills offline dev, slows every query by tailnet latency, and one teammate's destructive test affects everyone.
- **Tailscale on the host OS** (instead of the sidecar pattern). Same end result but requires host install + manual `pg_hba.conf` gymnastics to restrict the Postgres listener to the Tailscale interface. The sidecar pattern is self-contained in compose.

## Trade-offs

- **The host machine must be on to refresh.** Local dev keeps working when the host is offline; only the refresh blocks. The host's `tailscale_state` volume preserves the device identity across `docker compose down/up`, so the tailnet IP and `flat-chat-db` name are stable.
- **Refresh is destructive.** `./scripts/refresh-db.sh` drops the teammate's local DB before restoring. Anyone with local-only data that matters should dump it first.
- **Auth lives at two layers.** Tailnet membership controls who can reach the DB (gated by the admin console). Postgres roles control DB access. Teammates need both — the password is `POSTGRES_PASSWORD` from `.env`, same as everyone else's local copy.
- **`TS_AUTHKEY` is required only on the host.** The compose interpolation uses `${TS_AUTHKEY:?...}` inside `docker-compose.host.yml` so a missing key on the host fails fast. Teammates without `COMPOSE_FILE` set don't load the overlay and never need the key.

## Setup (one-time, host)

1. Sign up at [tailscale.com](https://tailscale.com), install Tailscale on a personal device for admin access.
2. In the admin console, edit ACL policy to allow the tag:
   ```json
   "tagOwners": {
     "tag:flat-chat-db": ["autogroup:admin"]
   }
   ```
3. Generate an auth key at `https://login.tailscale.com/admin/settings/keys` — reusable, non-ephemeral, tagged `tag:flat-chat-db`.
4. Add to your `.env`:
   ```
   COMPOSE_FILE=docker-compose.yml:docker-compose.host.yml
   TS_AUTHKEY=tskey-auth-...
   ```
5. `docker compose up --build` — the tailscale container registers as `flat-chat-db`, postgres lives in its netns.
6. Invite teammates as users in the admin console.

## Setup (teammate)

1. Accept the Tailscale invite, install the client on your host OS, sign in.
2. `git clone`, `cp .env.example .env`, fill in your API keys.
3. `docker compose up --build` — full local stack running against an empty Postgres.
4. `./scripts/refresh-db.sh` — pulls current data from the host's tailnet DB into your local Postgres.
