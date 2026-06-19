# flat-chat — Hackathon Starter

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

> **🏗️ This is the hackathon starter.** The agent has been removed and replaced
> with a swappable seam + a no-LLM placeholder. The whole app — search, listings,
> geo-context data, map, cards, chat UI — works out of the box; your job is to
> bring your own agent framework and make the search smart. **Start with
> [`HACKATHON.md`](HACKATHON.md).**

## Quick Start

```bash
cp .env.example .env                 # works as-is; no LLM keys needed for the placeholder agent
./scripts/restore-db-snapshot.sh     # load the shared listings DB (see HACKATHON.md for the snapshot file)
docker compose up --build
```

Open [http://localhost](http://localhost). First launch takes a couple of minutes (image builds).
Type a message (e.g. "flats in Kreuzberg") — the placeholder agent returns listings onto the map.

Manual data ingestion (cron-triggered in prod):

```bash
docker compose --profile ingestion run --rm ingestion
```

## Architecture

![Architecture](architecture.png)

Source: [`architecture.drawio`](architecture.drawio) — edit in draw.io Desktop or app.diagrams.net, then run `./render.sh` to regenerate the PNG.

## Project Structure

```
flat-chat/
├── docker-compose.yml          # Orchestrates all services
├── nginx/                      # Reverse proxy — only port 80 exposed to host (also serves /tiles/)
├── data/tiles/                 # Protomaps .pmtiles extract for MapLibre (bind-mounted into nginx)
├── services/
│   ├── frontend/               # React + Vite + CopilotKit + MapLibre — see services/frontend/src/
│   ├── backend/                # FastAPI + AG-UI streaming; agent seam in chat/backend.py — see services/backend/README.md
│   ├── ingestion/              # Cron-triggered data ingestion (scrapers + iron/bronze/silver loaders)
│   └── postgres/               # Custom image: PostgreSQL + pgvector + PostGIS
└── agent-compound-docs/        # Architecture decisions, plans, design conversations
```

## Tech Stack

| Layer            | Technology                                                                                          |
|------------------|-----------------------------------------------------------------------------------------------------|
| Frontend         | React, Vite, TypeScript, Tailwind, **CopilotKit (AG-UI)**, **MapLibre GL JS v5** + `@vis.gl/react-maplibre` |
| Backend          | FastAPI, SQLAlchemy, Alembic, **AG-UI Protocol** (`ag_ui`) streaming over SSE                       |
| LLM / Agent      | **Bring your own** — implement the `AgentBackend` seam (`chat/backend.py`). Starter ships a no-LLM placeholder. |
| Embeddings       | Jina v3 (`retrieval.query` task LoRA)                                                               |
| Database         | PostgreSQL + pgvector (semantic search) + PostGIS (geo)                                             |
| Map tiles        | Self-hosted **Protomaps** `.pmtiles` (Berlin extract) — served by nginx at `/tiles/`                |
| Observability    | Phoenix (Arize) via OpenInference + OpenTelemetry — UI at `:6006`                                   |
| Infrastructure   | Docker, Docker Compose, Nginx                                                                       |

## Data Pipeline

Listings flow through three Postgres tiers — **iron** (raw scraped cards) → **bronze** (raw scraped detail dumps) → **silver** (normalized `listings`). Node scrapers (puppeteer) write directly to iron and bronze; a Python transformer reads bronze and upserts silver. The silver `listings` table is what the search service queries.

See **[services/ingestion/README.md](services/ingestion/README.md)** for commands, JSON replay, and cursor-resume semantics.

## Where to look next

- **[`CLAUDE.md`](CLAUDE.md)** — project-wide conventions, architecture notes, Pydantic AI patterns.
- **[`services/backend/README.md`](services/backend/README.md)** — backend dev workflow, API reference, config table.
- **[`agent-compound-docs/decisions/`](agent-compound-docs/decisions/)** — what we chose and why (agent framework, LLM tool result design, deployment, …).

## MVP Scope

- User describes apartment requirements to the chatbot.
- Iterative refinement through conversation.
- Results stream into a persistent map + apartment cards artifact alongside the chat (chat-host layout, desktop-only).
- Berlin only.
