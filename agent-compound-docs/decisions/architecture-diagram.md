# Architecture Diagram

The system architecture diagram for this project lives at the repo root:

- `architecture.drawio` — **source of truth**, editable in [draw.io desktop](https://www.drawio.com/) or [app.diagrams.net](https://app.diagrams.net).
- `architecture.png` — rendered PNG (2400 px wide), for README / docs / Slack. **Do not hand-edit** — re-render from the .drawio file.

**Do not regenerate the .drawio from scratch.** Edit `architecture.drawio` in draw.io, then re-render the PNG. If a future Cowork session is asked to "redo the diagram", start from the current .drawio file, not a blank canvas.

## Layout

The diagram is laid out as horizontal layers, with one dashed VPS boundary that everything internal lives inside:

- **Cloudflare** sits outside the VPS as a vertical gateway — Web Users flow through it on their way to Nginx.
- **Frontend** (React/Vite) is served by Nginx at `/` — there is intentionally **no arrow** from Frontend to Backend; in-browser fetches go back out through Cloudflare → Nginx → `/api/`.
- **Backend** is split into clear layers:
  - **Users layer** (top) — `users` module, talks only to the User-data zone of Postgres.
  - **Chat / Search layer** (middle) — `chat / agent` on the left talks only to `search` on its right; `search` is the only thing that reads the Listings + Berlin enrichment zones.
- **Ingestion Worker** sits in its own row below the backend, isolated from the backend modules. It writes to the Listings and Berlin enrichment zones only.
- **PostgreSQL** is the right column, partitioned into three visually distinct zones: User data, Listings, Berlin enrichment.
- **LLM Providers** sit below the VPS, called only by `chat / agent` (indigo arrow).
- **External listing sources + Berlin Open Data** sit to the right of the VPS, feeding the Ingestion Worker.

## Conventions

- **Arrows:**
  - Solid gray `#64748b` — HTTP request / call (request flow, DB reads/writes from backend).
  - Solid indigo `#6366F1` — LLM provider call (chat → LLM Providers).
  - Solid green `#10B981` — data ingestion (sources → Worker → Postgres).
- **VPS boundary:** dashed rounded rectangle labelled `HETZNER VPS · docker-compose`. Anything outside it is external (users, Cloudflare, LLM providers, data sources).
- **Cards:** rounded rectangles, subtle drop shadow, brand-coloured stroke (React cyan, Nginx green, Postgres blue, LLM indigo, Ingestion green, Cloudflare orange).

## How to render the PNG

Run from the repo root, after installing [draw.io Desktop](https://github.com/jgraph/drawio-desktop/releases/latest):

```bash
./render.sh
```

That invokes draw.io Desktop's CLI and writes `architecture.png` at 2400 px wide.

## How to iterate

1. Open `architecture.drawio` in draw.io (desktop or web).
2. Edit visually — drag cards, change labels, add components.
3. Save the .drawio file back to the repo.
4. Re-render the PNG using one of the methods above.
5. Visually review the PNG.

## Things that have already been decided (don't re-litigate without a reason)

- Frontend is **React + Vite + TypeScript**, not Next.js.
- Ingestion is **Puppeteer + Python**, not pure-Python.
- LLM dispatch is **Pydantic AI** with native provider clients — selection lives in `services/backend/src/flat_chat/chat/providers/__init__.py` (the single seam). Two providers are wired today: Anthropic-direct (preferred when its key is set, for native prompt caching) and Azure OpenAI as fallback. No LiteLLM in the request path.
- Postgres uses **both pgvector and PostGIS** in the same database — not split into separate stores.
- Cloudflare sits **in front of** the VPS for DNS / TLS / WAF and to hide the VPS IP.
- The frontend talks to the backend **only via the user's browser**, through the same Cloudflare → Nginx ingress — there is no direct Frontend → Backend arrow in the diagram.
