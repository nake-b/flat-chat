# CLAUDE.md

Project context for Claude Code.

## Project Overview

Berlin Apartment AI Assistant — a chatbot to help Berliners find apartments quickly and make informed decisions through conversational search.

## Tech Stack

- **Frontend:** React, Vite, TypeScript
- **Backend:** FastAPI, SQLAlchemy, Alembic
- **Database:** PostgreSQL + pgvector (vector search, structured and geo data)
- **Infrastructure:** Nginx (reverse proxy), Docker, Docker Compose
- **Python:** 3.14 (uv + pyproject.toml for dependency management)
- **LLM/Search:** TBD

## Project Structure

```
services/frontend/     → React + Vite chat UI, served by Nginx as static files
services/backend/      → FastAPI app, src/app/ package layout
services/ingestion/    → Batch data ingestion, triggered by cron (not a long-running service)
nginx/                 → Reverse proxy config (routes / → frontend, /api/ → backend)
compound-docs/         → Architecture decisions and deployment guide
```

## Running the Project

```bash
docker compose up --build        # Start all services at http://localhost
docker compose --profile ingestion run --rm ingestion   # Run ingestion manually
```

## API Conventions

- All API routes are prefixed with `/api/`
- Chat uses an app-level REST API (not OpenAI-style): `POST /api/conversations`, `POST /api/conversations/{id}/messages`
- The frontend uses relative URLs (`/api/...`) — works via both Vite dev proxy and Nginx

## Architecture Notes

- Nginx is a separate Docker Compose service (not embedded in the frontend container)
- Only Nginx exposes a port (80) — all other services are internal
- PostgreSQL is defined in docker-compose.yml only (no dedicated directory)
- Backend owns the DB schema via Alembic migrations
- The architecture is evolving iteratively — question choices, suggest improvements, flag concerns

## agent-compound-docs/

Architecture decisions and guides live in `agent-compound-docs/`. When making significant architectural decisions, document them there with what was chosen, what was rejected, and why. Read existing docs before proposing changes to areas they cover.

## MVP Scope

- User describes apartment requirements to chatbot
- Results displayed on a map
- Iterative refinement through conversation

## Out of Scope

- Cities other than Berlin
