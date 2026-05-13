# flat-chat

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

## Quick Start

```bash
cp .env.example .env    # first time only
docker compose up --build
```

Open [http://localhost](http://localhost).

## Architecture

![Architecture](architecture.png)

## Project Structure

```
flat-chat/
├── docker-compose.yml          # Orchestrates all services
├── nginx/                      # Reverse proxy config
├── services/
│   ├── frontend/               # React + Vite + TypeScript
│   ├── backend/                # FastAPI + SQLAlchemy + Alembic
│   └── ingestion/              # Data ingestion (cron-triggered)
```

## Tech Stack

| Layer          | Technology                        |
|----------------|-----------------------------------|
| Frontend       | React, Vite, TypeScript           |
| Backend        | FastAPI, SQLAlchemy, Alembic      |
| Database       | PostgreSQL + pgvector             |
| Infrastructure | Docker, Docker Compose, Nginx     |

## Data Pipeline

Listings flow through three Postgres tiers — **iron** (raw scraped cards) → **bronze** (raw scraped detail dumps) → **silver** (normalized `listings`). Node scrapers (puppeteer) write directly to iron and bronze; a Python transformer reads bronze and upserts silver.

See **[services/ingestion/README.md](services/ingestion/README.md)** for commands, JSON replay, and the cursor-resume semantics.
