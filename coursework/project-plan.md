# Flat Chat — Project Plan

**A conversational apartment-search assistant for Berlin.**

| | |
|---|---|
| **Project** | Flat Chat |
| **Course** | Softwareprojekt: LLMs (SWP LLMs) — Freie Universität Berlin |
| **Team** | Nadir Bašić · Luka Radulovikj · Eric Germersdorf |
| **Supervisor** | Tim |
| **Document** | Project Plan · v1.0 |
| **Date** | 12 May 2026 (written up after the 10 May kickoff) |
| **Status** | Living document — revisited at our weekly sync |

> **In one sentence:** help Berliners find a good apartment quickly and decide with confidence, through a single chat interface backed by a context-enriched view of the market.

This is our plan for the semester: what we're building, for whom, how it's put
together, who owns what, and how we'll evaluate and demo it. We're keeping it
agile — a few things below are marked *(to decide)* on purpose, so we can make
those calls as we learn more. We'll revise this document in place rather than
letting it go stale.

---

## 1. Problem & motivation

Finding an apartment in Berlin is slow and fragmented. Listings are spread across
several portals, each with its own filters, and none of them show the context
that actually decides whether a flat is worth applying for — how quiet it is, how
green, how well connected, how close to a school. Picky searchers end up juggling
tabs and comparing listings by hand for hours; the fallback is a real-estate
agent, which is expensive and inconsistent.

Most existing German tools optimise for *speed of notification* — "be the first
to see a new listing". We want to optimise for *ease and understanding*: a
comprehensive, honest overview and a conversation that helps you decide. The
goal is something that behaves a bit like a good agent would — an
"apartment-hunter ChatGPT" scoped to Berlin.

Modern LLMs make this newly practical: they can turn plain language into a
structured search, ground answers in real data, and hold a multi-turn
refinement conversation. Berlin's open data (environment, transit, points of
interest) gives us the context layers that set the product apart.

---

## 2. Target users & user stories

Four searchers we're designing for:

- **The newcomer.** *"I'm moving to Berlin in a month and haven't found a flat.
  All these websites and forms have gotten me so confused — I wish I could just
  ask ChatGPT to find me one."*
- **The picky optimiser.** *"I want a nice area, a good commute, three bedrooms.
  I spend hours researching and comparing day to day."*
- **The overwhelmed applier.** *"I'm tired of juggling sites and endless
  listings. I want clear, honest summaries so I can quickly find and apply to
  places that actually match."*
- **The family.** *"We need somewhere long-term, but finding one that meets all
  our needs is hard. Agents are expensive and most aren't very useful."*

**The core job:** *describe the apartment I want, see the good matches on a map
with the context that matters, and refine by talking — until I'm confident
enough to apply.*

---

## 3. Scope

Prioritised with MoSCoW. The must-haves are the core; everything below is
sequenced but can be cut if time runs short.

**Must have (MVP)**
- Describe an apartment to a chatbot and get back matching listings.
- Results shown on an interactive Berlin map *and* as detail cards.
- Refine the search conversationally — narrow, widen, pivot — without re-typing.
- Broad coverage: aggregate the major Berlin portals into one clean, de-duplicated dataset.
- A first slice of neighbourhood context applied to results.

**Should have**
- Rich neighbourhood context per listing: noise, greenery, transit, and proximity to schools, hospitals, parks, water.
- Grounded, honest summaries — the agent explains matches and trade-offs and never invents facts about a listing.
- Conversations that persist and can be reloaded.

**Nice to have**
- Map "lenses" — colour every result by one dimension (e.g. commute time or distance to a place) for quick comparison.
- Named-place and landmark search ("near the TU campus", "along the Spree").
- Accounts and bookmarks.
- Some emergent agent behaviour — proactive suggestions, reasoning across the whole result set.

**Stretch**
- Automated daily searches with notifications (e.g. a Telegram bot).
- Assisted application drafting.
- Exposing the search as an API for other clients.

**Out of scope**
- Cities other than Berlin — depth over breadth.
- Price valuation / fair-pricing modelling — we surface listings, we don't appraise.
- Mobile / responsive layouts — desktop-first for this course.

**What "done" looks like**
- A first-time user can describe a flat and reach a shortlist on the map in one conversation, unaided.
- Coverage across the target portals is broad and refreshed on a schedule.
- The agent's answers stay grounded — no invented listing facts in our evaluation.
- Positive feedback from user testing (see §9, §11).

---

## 4. What the system does

The functional core, in plain terms:

- Takes a natural-language query and returns matching listings.
- Renders matches as map markers and as detail cards (price, rooms, size, location, image).
- Lets the user refine the active result set conversationally.
- Aggregates listings from several portals into one de-duplicated dataset.
- Enriches listings with neighbourhood context (noise, greenery, transit, nearby points of interest).
- Produces grounded summaries and comparisons.
- Lets the user open a listing for full detail, and reload a past conversation.

Qualities we care about (non-functional):

- **Responsive.** Search should feel quick, and the chat should stream so the UI is never blank while the model thinks.
- **Affordable.** LLM spend stays bounded — caching, a non-LLM path for pure structured search, and per-user usage limits.
- **Robust.** Degrade gracefully if a data source, the LLM, or a routing engine is unavailable.
- **Maintainable.** Layered, modular code with automated tests on the deterministic parts and CI on every PR.
- **Usable.** A desktop chat + map experience a first-timer can use without instructions.
- **Debuggable.** We can trace the agent → search → SQL path when the LLM does something surprising.
- **Privacy-minimal.** Store only what we need — accounts, sessions, bookmarks; no personal data about the people who post listings.

---

## 5. System overview & architecture

We're keeping the architecture agile and making decisions as we go. The shape
below is our current best plan; interfaces are chosen so parts can change
without rippling.

**Early decisions**
- **Modular monolith backend.** One deployable for simplicity, but the search /
  chat layer is separated cleanly enough that we could serve other clients later
  without a rewrite.
- **One database for everything.** At our scale, PostgreSQL — with pgvector for
  semantic search and PostGIS for geo — handles structured, vector, and spatial
  data in a single store. No separate vector DB.
- **Ingestion as a scheduled job.** A pipeline that collects listings
  respectfully (low request volume) and keeps the dataset fresh.
- **Small footprint hosting.** Runs entirely in Docker; deployed to a modest
  self-hosted server, fronted by Cloudflare for TLS and basic protection.
  *(Exact host to decide.)*

**Component sketch**

```
   Browser ─► Cloudflare ─► Nginx (reverse proxy)
                               │
                    ┌──────────┴───────────┐
                    │  Frontend (React SPA) │
                    │  chat · map · cards   │
                    └──────────┬───────────┘
                     streaming chat + REST reads
                    ┌──────────┴───────────┐
                    │  Backend (FastAPI)    │
                    │  api → chat → search  │
                    │       → core          │
                    │  • LLM agent (tools)  │
                    │  • structured search  │
                    └──────────┬───────────┘
                               │
                    ┌──────────┴───────────┐
                    │  PostgreSQL           │
                    │  + pgvector + PostGIS │
                    └──────────▲───────────┘
                               │
                    ┌──────────┴───────────┐
                    │  Ingestion pipeline   │
                    │  scrape → clean →     │
                    │  enrich → embed       │
                    └───────────────────────┘
```

**Data pipeline.** A layered flow so each stage runs and tests independently:

1. **Raw capture** — per-portal scrapers save listings as they come; where a
   portal splits overview and detail, we grab cards first, then details.
2. **Clean** — parse into a common schema, de-duplicate, fill in missing
   coordinates, and drop any personal data of posters.
3. **Enrich** — join each listing to Berlin open data: noise, greenery, transit
   connectivity, and nearby schools, hospitals, parks, water, plus its district.
4. **Embed** — compute text embeddings for semantic ranking.

Initial listing sources: **InBerlinWohnen, Kleinanzeigen, WG-Gesucht** (more to
follow). Context sources: **Berlin GDI (WFS)** for environmental and
administrative layers, **VBB (GTFS)** for transit, and **OpenStreetMap** for
landmarks.

**Agent design.** A tool-using agent interprets the user's language and decides
what to do — build a search, refine the result set, open a listing, explain
trade-offs. Structured search is a *tool*, not the model's job: the LLM owns
interpretation, deterministic SQL owns retrieval. That keeps results correct and
cheap and gives us a fast path that doesn't need the model. The agent is
constrained to answer only from retrieved data — no invented listing facts.

**Two channels to the UI.** The live chat stream carries interpretation and the
small bit of state the map and chat need; durable detail reads (a listing's full
info) go over plain REST so they're cacheable and shareable. This keeps the
streaming payload light.

---

## 6. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Edge / infra | Cloudflare, Nginx | Free TLS + basic protection; simple, proven proxy |
| Frontend | React + TypeScript (Vite, Tailwind) | Fast SPA dev, strong typing, good map ecosystem |
| Map | MapLibre GL JS | Open-source vector maps; self-hostable tiles later |
| Backend | FastAPI (Python) | Async, typed, first-class streaming |
| ORM | SQLAlchemy | Mature; works in the app, scripts, and tests |
| Database | PostgreSQL + pgvector + PostGIS | One store for structured, vector, and geo data |
| Scraping | Puppeteer (JS) | Handles JS-heavy portals; card-then-detail crawl |
| Agent / LLM | A Pydantic-based agent layer | Typed tools, structured output, retries |
| Packaging | Docker, Docker Compose, GitHub Actions | Reproducible local runs and CI/CD |

**To decide:** which LLM provider(s) to run behind the agent, and how much of
search stays non-LLM. We'll keep the provider choice behind a thin seam so we
can switch without touching call sites. Self-hosted map tiles and a routing
engine (for any commute feature) are open too.

---

## 7. Timeline

The semester has two headline checkpoints:

- **Midterm** — a demoable vertical slice (describe a flat → map + cards + basic
  context, over a clean dataset and working search), plus a recorded demo video.
- **Final** — the full MVP deployed, documented (including API docs), and
  presented live with a backup video.

Between them we build out the should-haves (context depth, grounded summaries,
persistent conversations) and selected nice-to-haves (map lenses, named-place
search, accounts/bookmarks), and harden for deployment. We track the week-to-week
plan in our shared tracker rather than pinning it here, so this document stays
about the *what* and the checkpoints rather than a schedule that drifts.

---

## 8. Roles & ways of working

| Role | Person | Focus |
|---|---|---|
| Product / PM | Eric | Scope, timeline, data-source research, frontend |
| Engineer | Luka | Ingestion, scrapers, DB; chatbot |
| Engineer | Nadir | Search + chatbot, backend, testing & deployment |

Ownership is a *lead*, not a silo — we pair and review across boundaries.

We keep the process light and agile: most decisions are made autonomously and
sanity-checked in PR review or at the weekly sync; bigger calls we make together.
Work flows through feature branches → PR → review → merge, with CI green before
merge, and `main` always demoable. We keep short notes on the decisions that
matter so the reasoning survives.

---

## 9. Testing & quality

- **Test the deterministic layers properly.** Ingestion, search SQL, and the API
  get automated tests. We favour tests that actually run SQL/HTTP against a real
  fixture over compile-or-mock tests — a query can "compile" and still be
  rejected at runtime.
- **Fix-with-a-test.** When a bug ships that a test should have caught, the fix
  lands with the regression test.
- **Check the LLM parts separately.** Lightweight eval/smoke runs exercise the
  agent + chat workflow (grounding, tool choice, refinement quality) — alongside,
  not instead of, the unit/integration tests.
- **CI on every PR** gates merges to `main`.
- **User testing** ahead of each checkpoint.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Data-source access / breakage (portals change or block) | Medium | Medium | Isolated per-source scrapers; low request volume; accept some staleness; graceful skips |
| LLM cost | Medium | High | Non-LLM path for structured search; caching; per-user usage limits |
| Scope too large | High | Low | Ruthless MVP focus; nice-to-haves are cut-able |
| Geo-data quality (bad geometries, geocoding gaps) | Medium | Medium | Validate geometries; coverage gates on context joins |
| LLM / network flakiness | Medium | Medium | Retries, timeouts, graceful degradation, clear error states |
| Team bandwidth (course load) | Medium | Medium | Clear owners; demoable `main`; buffer in the timeline |

---

## 11. Evaluation & demo

**How we'll judge it**
- User testing — real people describe a flat and reach a shortlist; we watch task success and gather feedback.
- Listing coverage and freshness across the target portals.
- Relevance of search results to the query.
- Agent quality — grounded answers, correct tool choice, sensible refinement.

**Midterm demo (video)** — a short, honest walkthrough of the vertical slice:

1. The problem — many tabs, no context, hours of comparison.
2. Type a real query ("bright 2-room in a quiet, green area, good U-Bahn access, under €1,400 warm"); show the agent interpreting it.
3. Results appear on the map *and* as cards, with basic context visible.
4. One conversational tweak ("actually, closer to Kreuzberg") updates the map in place.
5. The agent summarises the top matches and a trade-off.
6. One diagram: scrape → clean → enrich → search → chat.
7. What's next.

**Final presentation** — a live demo where we ask someone in the audience to
describe their ideal apartment, prompt it live, browse the map, and open a
listing (and show bookmarks if built). We rehearse against the deployed URL and
keep a backup video in case the Wi-Fi fails.

**API documentation.** Because the backend is built to be callable, its public
surface is a first-class deliverable: FastAPI's auto-generated OpenAPI
(interactive docs) plus a short guide covering the conversation lifecycle, the
chat stream, listing reads, and health — with request/response examples.
Delivered alongside the final.

---

## 12. Responsible data use

- **No personal data of posters.** We store listings, not people; scrapers are
  set up to leave out posters' personal/contact data.
- **No sensitive socio-demographic layers.** Our neighbourhood context stays with
  environmental and connectivity data (noise, greenery, transit, points of
  interest) — we deliberately avoid "who lives where" style layers, which are
  ethically fraught for apartment search.
- **Honesty by design.** The agent answers from retrieved data and doesn't
  fabricate listing facts or oversell a neighbourhood.
- **Minimal accounts.** Only what the product needs — sessions and bookmarks.

---

## 13. Deliverables

- [ ] This project plan (kept up to date)
- [ ] Working MVP: conversational search → map + cards → refinement
- [ ] Multi-portal ingestion producing a clean, enriched dataset
- [ ] Neighbourhood context (at least the basic slice by the midterm)
- [ ] Deployed URL
- [ ] Automated tests + green CI
- [ ] API documentation (OpenAPI + short guide)
- [ ] Midterm demo video
- [ ] Final presentation + live demo + backup video
- [ ] User-testing results

---

## Appendix

**Glossary**
- *Listing* — a single apartment offer aggregated from a portal.
- *Context / enrichment* — neighbourhood data joined to a listing.
- *Agent* — the tool-using LLM that interprets language and runs the search.
- *Lens* (nice-to-have) — a map colouring by one value, e.g. commute time.

**Source documents** — kickoff protocol (10 May 2026) and the draft Project Doc.

**Change log**

| Version | Date | Change |
|---|---|---|
| 1.0 | 12 May 2026 | Initial baseline — from the kickoff protocol and the draft Project Doc. |

*Living document. Items marked "to decide" are deliberate deferrals, revisited at
the weekly sync.*
