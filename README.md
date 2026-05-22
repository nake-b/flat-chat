# flat-chat

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

## Quick Start

```bash
cp .env.example .env    # then fill in OPENROUTER_API_KEY, OPENROUTER_MODEL, JINA_API_KEY
docker compose up --build
```

Open [http://localhost](http://localhost). First launch takes a couple of minutes (image builds).

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
├── nginx/                      # Reverse proxy — only port 80 exposed to host
├── services/
│   ├── frontend/               # React + Vite + TypeScript (built into shared volume)
│   ├── backend/                # FastAPI + Pydantic AI agent + SearchService — see services/backend/README.md
│   ├── ingestion/              # Cron-triggered data ingestion
│   └── postgres/               # Custom image: PostgreSQL + pgvector + PostGIS
└── agent-compound-docs/        # Architecture decisions, plans, design conversations
```

## Tech Stack

| Layer            | Technology                                                        |
|------------------|-------------------------------------------------------------------|
| Frontend         | React, Vite, TypeScript                                           |
| Backend          | FastAPI, SQLAlchemy, Pydantic AI                                  |
| LLM              | Pydantic AI agent → OpenRouter (presets for server-side fallback) |
| Embeddings       | Jina v3 (`retrieval.query` task LoRA)                             |
| Database         | PostgreSQL + pgvector (semantic search) + PostGIS (geo)           |
| Observability    | Phoenix (Arize) via OpenInference + OpenTelemetry — UI at `:6006` |
| Infrastructure   | Docker, Docker Compose, Nginx                                     |

## Where to look next

- **[`CLAUDE.md`](CLAUDE.md)** — project-wide conventions, architecture notes, Pydantic AI patterns.
- **[`services/backend/README.md`](services/backend/README.md)** — backend dev workflow, API reference, config table.
- **[`agent-compound-docs/decisions/`](agent-compound-docs/decisions/)** — what we chose and why (agent framework, LLM tool result design, deployment, …).

## MVP Scope

- User describes apartment requirements to the chatbot.
- Iterative refinement through conversation.
- Berlin only.
