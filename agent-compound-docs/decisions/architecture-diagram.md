# Architecture Diagram

The system architecture diagram for this project lives at the repo root:

- `architecture.drawio` — **the single source of truth**. Editable in [draw.io desktop](https://www.drawio.com/) or [app.diagrams.net](https://app.diagrams.net). Currently holds the v5 topology: frontend-as-column left→right flow, routing engines above the backend, container subtitles, schema-ownership bands, contained DB-zone text.
- `architecture.png` — rendered PNG (~2400 px wide), for README / docs / Slack. **Do not hand-edit** — re-render from `architecture.drawio` via `./scripts/render.sh` (GUI) or the headless loop in [`editing-drawio-programmatically.md`](editing-drawio-programmatically.md).

The intermediate `architecture-v3/v4/v5.drawio` scratch files were **consolidated into `architecture.drawio` and deleted** (2026-07-09) — the evolution below is the record; the files themselves live in git history if ever needed.

## Version history

This log tracks how the current `architecture.drawio` evolved. It is prose history now — the numbered `.drawio` files it references no longer exist on disk (consolidated into `architecture.drawio`).

- **v1** (retired 2026-07-02) — deployed-on-a-VPS view: Web Users → Cloudflare (DNS/TLS/WAF, hides VPS IP) → nginx inside a dashed `HETZNER VPS · docker-compose` boundary.
- **v2** (2026-07-02, now `architecture.drawio`) — **local-first view**. Removed Cloudflare + its edges; Web Users → nginx directly (`HTTP :80`, `e_web_nginx`); boundary relabelled `localhost · docker compose up`. Backend/Postgres/ingestion unchanged from v1.
- **v3** (2026-07-03, `architecture-v3.drawio`) — **routing + schema-ownership pass** (verified via headless render loop; several review iterations):
  - **Schema rectangles.** Postgres zones are grouped by two dashed ownership rectangles drawn inside `pgBox`: `app · backend-owned` (indigo, wraps `1 · User data`) and `world · ingestion-owned` (green, wraps `2 · Listings` + `3 · Berlin enrichment`), each with a label in the gap above it. Replaced the earlier inline pills (rejected — looked cramped). Zones were redistributed to open a title→app gap, and the five horizontal contract arrows (`usersMod`→`pgUser`, `searchMod`→`pgList`/`pgBerlin` via recomputed exitY, `ingestListings`→`pgList`, `ingestBerlin`→`pgBerlin`) were **refit to Δ ≤ 0.1px** so the DB-as-contract grid stays straight.
  - **Backend modules.** `users` → `users · auth` (fastapi-users · JWT; dropped the stale "not yet implemented"); `search` → `search · listings` (shared read layer, `HTTP /api/listings + agent`); `chat / agent` gained a `lenses · routing · overlays` line; the Pydantic AI logo moved onto the `chat / agent` box.
  - **Routing engines = two containers.** `OSRM` (car) and `MOTIS` (transit) are drawn as two separate container cards — each with a Docker whale icon like the other services — inside a `Routing engines · internal` group below Postgres, inside the boundary. Edge `e_backend_routing` (backend → engines). Boundary height 440→550 (bottom 690); `llm` + `jina` sit external at y=720.
  - **Transit data on the right.** `routingData` (`Transit + street data` — VBB GTFS + Berlin OSM, `prep-routing.sh`) is stacked in the **right data-source column** under `Listing sources` and `Berlin Open Data`, feeding the engines via a green arrow. VBB GTFS is the same feed geo-context ingests.
  - Lesson from the blind first pass: **always render before declaring done.** Bugs caught only by rendering — routing cards missing `vertex="1"` (arrows into empty space), `chat / agent` text overflow, a stray `world` fragment from an old `pgBox` title, an orphaned Pydantic logo. All fixed. Render recipe: [`editing-drawio-programmatically.md`](editing-drawio-programmatically.md) § Rendering a preview.
  - **v3 layout revision (2026-07-08) — left-to-right flow + routing on backend level:**
    - **Frontend is now a column between nginx and backend** (was a wide card on top), giving a clean left→right request flow `Web Users → nginx → frontend → backend → postgres → ingestion`. The `nginx → frontend` edge (`serves /`) is a straight horizontal; the `nginx → backend` edge (`proxies /api/`) routes **below** the frontend column (waypoints under it) so it doesn't cut through the card. The old dashed `frontend → backend` logical arrow (`e_fe_be_logical`) was **removed** — the doc's invariant is that browser fetches leave via nginx, so there is intentionally no direct FE→BE arrow.
    - **Routing engines moved above the backend** (into the space the frontend vacated), so `backend → engines` (`routing · travel-time`) points **up** while `chat → LLM` / `search → Jina` point **down** — the two arrow families no longer intersect below the backend. OSRM (car) and MOTIS (transit) now carry their **brand logos** inside the cards (alongside the Docker whale). The **Transit + street data** source was raised to the top of the right-hand source column so its green `prep-routing.sh` arrow feeds the engines as one straight horizontal line; the decorative docker-compose logo moved to the bottom-right corner to clear that corridor.
    - **`users` backend module → `app`** (subtitle `users · bookmarks · sessions · auth`) to mirror the `app` DB schema; the matching Postgres zone `1 · User data → 1 · App data`. `chat / agent` tool line updated to `search · overlays · lenses · proximity` (the four capabilities).
    - Reminder from this pass: `mxGeometry` uses `width`/`height`, **not** `w`/`h` — setting the wrong keys silently no-ops the resize (caught only on render: giant logos).
  - Iterating further: edit the current `.drawio`, keep this changelog current, then `./scripts/render.sh <file>.drawio` (GUI) or the headless loop.
- **v5** (2026-07-09, `architecture-v5.drawio`) — **polish pass on the v3-after-hand-edits state** (v4 is the frozen starting snapshot):
  - **Frontend ↔ backend arrow (re-added, dashed).** A light dashed `frontend → backend` edge labelled `/api/ · via nginx` (two-line label so it fits the short inter-box gap) now indicates the browser's API traffic. It's deliberately dashed to signal *logical, not a direct wire* (the real path is browser → nginx → backend). This reverses the v3-revision removal — the earlier "no FE→BE arrow" invariant is relaxed to "no *solid* FE→BE arrow"; a dashed indicator is wanted.
  - **Container subtitles.** `backend` moved `FastAPI` out of the title into a subtitle (`FastAPI · Pydantic AI · async`). `Ingestion worker` gained the medallion pipeline as a subtitle (`bronze → silver → gold → platinum` + `Puppeteer + Python · medallion ETL`); its title block was dropped below the top-left Python/Node icons (`spacingTop=34`, `spacingLeft=16`) instead of being squeezed to their right.
  - **DB-zone text contained.** `2 · Listings data` and `3 · Berlin enrichment` subtitles were spilling *below* their 44 px boxes (they used `verticalAlign=top;spacingTop=10`). Switched both to `verticalAlign=middle` (matching `1 · App data`) so the two-line text centres inside. Refreshed the data: `1 · App data → users · conversations · bookmarks`; `3 · Berlin enrichment → noise · greenery · transit · POIs` (dropped the bogus "air" — there's no air-quality source; "POIs" covers the schools/kitas/parks/hospitals/water junction tables).
  - Data sources reviewed and left as-is — `Listing sources` (WG-Gesucht · Kleinanzeigen · HousingAnywhere · WohnInBerlin) matches the *wired* silver transformers (immowelt is bronze-only, intentionally omitted); `Berlin Open Data` and `Transit + street data` are accurate.
  - Same `width`/`height` (not `w`/`h`) gotcha as the v3 revision — verified via render.

**Do not regenerate the .drawio from scratch.** Edit `architecture.drawio` in draw.io, then re-render the PNG. If a future Cowork session is asked to "redo the diagram", start from the current .drawio file, not a blank canvas.

## Layout

The diagram is laid out as horizontal layers, with one dashed VPS boundary that everything internal lives inside:

The layout below describes the shared structure. In **v2** (current) Web Users hit **nginx directly on `:80`** — there is no Cloudflare gateway and the boundary is `localhost · docker compose up`, not a Hetzner VPS. The Cloudflare/VPS wording below is v1; see the Version breadcrumbs above for the exact v1→v2 deltas.

- **(v1)** **Cloudflare** sits outside the VPS as a vertical gateway — Web Users flow through it on their way to Nginx. **(v2)** Web Users → nginx directly (`HTTP :80`).
- **Frontend** (React/Vite) is served by Nginx at `/` — there is intentionally **no arrow** from Frontend to Backend; in-browser fetches go back out through Nginx → `/api/` (v1: Cloudflare → Nginx → `/api/`).
- **Backend** is split into clear layers:
  - **Users layer** (top) — `users` module, talks only to the User-data zone of Postgres.
  - **Chat / Search layer** (middle) — `chat / agent` (left) drives the `lenses · routing` and talks to `search · listings` (right); `search · listings` is the only thing that reads the Listings + Berlin enrichment zones.
- **Ingestion Worker** sits in its own row below the backend, isolated from the backend modules. It writes to the Listings and Berlin enrichment zones only.
- **PostgreSQL** is the right column, partitioned into three zones grouped by **schema ownership** (v3): `app` (User data — backend-owned, indigo) and `world` (Listings + Berlin enrichment — ingestion-owned). The backend parts mirror the split: `users · auth` ↔ `app`, `search · listings` ↔ `world`.
- **LLM Providers + Jina** sit below the boundary, called by `chat / agent` (LLM, indigo) and `search`/ingestion (Jina, indigo).
- **Routing engines** (v3, OSRM car + MOTIS transit) are an **internal** card below Postgres, inside the boundary, called by the backend (`e_backend_routing`). Their **Berlin OSM + VBB GTFS** inputs are an external card fed by `prep-routing.sh` (green).
- **External listing sources + Berlin Open Data** sit to the right of the boundary, feeding the Ingestion Worker.

## Conventions

- **Arrows:**
  - Solid gray `#64748b` — HTTP request / call (request flow, DB reads/writes from backend).
  - Solid indigo `#6366F1` — LLM provider call (chat → LLM Providers).
  - Solid green `#10B981` — data ingestion (sources → Worker → Postgres).
- **Compose boundary:** dashed rounded rectangle. v2 labels it `localhost · docker compose up` (v1: `HETZNER VPS · docker-compose`). Anything outside it is external (users, LLM providers, data sources).
- **Cards:** rounded rectangles, subtle drop shadow, brand-coloured stroke (React cyan, Nginx green, Postgres blue, LLM indigo, Ingestion green). (v1 also had a Cloudflare-orange card, dropped in v2.)

## How to render the PNG

Run from the repo root, after installing [draw.io Desktop](https://github.com/jgraph/drawio-desktop/releases/latest):

```bash
./scripts/render.sh
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
- LLM dispatch is **Pydantic AI** with native provider clients — selection lives in `services/backend/src/flat_chat/chat/providers/__init__.py` (the single seam). Three providers are wired today: OpenAI (standard, non-Azure), Anthropic-direct (native prompt caching), and Azure OpenAI; preference order OpenAI > Anthropic > Azure (first key set wins). No LiteLLM in the request path.
- Postgres uses **both pgvector and PostGIS** in the same database — not split into separate stores.
- **(v1 only, superseded by v2)** Cloudflare sat **in front of** the VPS for DNS / TLS / WAF and to hide the VPS IP. v2 is the local-dev topology with no Cloudflare and no Hetzner — nginx is the ingress on `:80`.
- The frontend talks to the backend **only via the user's browser**, through the Nginx ingress — there is no direct Frontend → Backend arrow in the diagram.
