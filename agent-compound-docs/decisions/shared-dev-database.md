# Shared dev database

## Problem

The team needs consistent dev data across machines. The prior workflow — exporting the Postgres container as a tarball and shipping it through random fileshares — is slow, lossy, and there's no source of truth for "what's the current dataset everyone should be on".

## Decision

Run Postgres on Luka's local box with a **Tailscale sidecar container** in `docker-compose.yml`. The DB is reachable to teammates at `flat-chat-db.<tailnet>.ts.net:5432` over the tailnet; not reachable from anywhere else.

Pattern:
- A `tailscale` service joins the tailnet on boot via `TS_AUTHKEY`, registers as hostname `flat-chat-db` with `tag:flat-chat-db`.
- The `postgres` service uses `network_mode: "service:tailscale"`, joining the tailscale container's network namespace. Postgres listens on the tailnet IP — no port is published to the host's public interfaces.
- The tailscale service has a docker-network alias of `postgres`, so `backend` and `ingestion` keep using `postgres:5432` and `DATABASE_URL` doesn't change.
- Port `5432` is published only on `127.0.0.1` so local GUI tools (TablePlus, DataGrip) still work without installing Tailscale on the host.

## Rejected alternatives

- **Azure Database for PostgreSQL Flexible Server (~$12–15/month).** Storage is free at our scale (~30MB); the cost is a 24/7 1-vCPU VM that managed Postgres can't scale to zero on. Overkill for "share a seed dataset".
- **Azure Blob Storage + `make seed` target (~$0/month).** Keeps the dump-and-restore dance — just centralises the fileshare. Doesn't give a live shared DB; everyone runs their own Postgres and drifts again as soon as ingestion runs.
- **Neon free tier (serverless Postgres, $0).** Would work technically (pgvector + postgis supported, scale-to-zero), but lives outside the Azure RG and adds another vendor to the trust boundary.
- **Tailscale on the host machine** (instead of the sidecar pattern). Same end result but requires host install + manual `pg_hba.conf` gymnastics to restrict the Postgres listener to the Tailscale interface. The sidecar pattern is self-contained in `docker-compose.yml`.

## Trade-offs

- **Single point of failure: Luka's machine must be on for teammates to query.** This is the explicit social contract — "ping me when you need data". If the team grows or this becomes a bottleneck, the same compose stack moves to a B1s Azure VM (~$8/month) with zero code changes.
- **Auth lives at two layers.** Tailnet membership controls network reachability (gated by the admin console). Postgres roles control DB access (gated by `pg_hba.conf` + `POSTGRES_PASSWORD`). Teammates need both.
- **`TS_AUTHKEY` is required to bring up the stack.** The compose interpolation uses `${TS_AUTHKEY:?...}` so a missing key fails fast with a clear message rather than silently exposing Postgres.

## Setup (one-time, host owner)

1. Sign up at [tailscale.com](https://tailscale.com), install Tailscale on a personal device for admin access.
2. In the admin console, edit ACL policy to allow the tag:
   ```json
   "tagOwners": {
     "tag:flat-chat-db": ["autogroup:admin"]
   }
   ```
3. Generate an auth key at `https://login.tailscale.com/admin/settings/keys` — reusable, non-ephemeral, tagged `tag:flat-chat-db`.
4. Add to `.env`: `TS_AUTHKEY=tskey-auth-...`
5. `docker compose up --build` — the tailscale container registers, then postgres boots inside its netns.
6. Invite teammates as users in the admin console. Once they install Tailscale and join the tailnet, they reach the DB at `flat-chat-db.<your-tailnet>.ts.net:5432`.

## Setup (teammate)

1. Accept the Tailscale invite, install the client, sign in.
2. Connect to Postgres: `psql -h flat-chat-db.<tailnet>.ts.net -U flat_chat -d flat_chat`
