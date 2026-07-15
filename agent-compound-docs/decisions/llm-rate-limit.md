# LLM rate limiting & per-user usage budgets

Where rate limiting belongs in the stack, and how to build a **per-user LLM
usage budget** on top of Pydantic AI's per-run `UsageLimits`.

Status: **built** (branch `feat/llm-rate-limiting`). When this doc was first
written the app had a dummy `get_user_id()`, no `app` schema, and no public
exposure, so §3 was deferred. All three preconditions then landed, and with a live
`OPENAI_API_KEY` making spend real money on a public origin, all three protections
were implemented — §4 per-run backstop, nginx per-IP limits, and the §3 per-user
budget. The preconditions that unblocked it:

- **Real auth** — `get_user_id()` (`core/dependencies.py`) resolves a real
  fastapi-users identity from a signed JWT cookie. Distinct users now exist.
- **`app` schema** — backend-owned + migrated (`0001_app_users_sessions`,
  `0002_app_bookmarks`). A usage ledger is one more migration.
- **Public deployment** — the stack runs behind a **Cloudflare Tunnel**
  (`docker-compose.prod.yml`), so abuse/cost is a real concern, not hypothetical,
  and the origin isn't directly reachable (see the CF caveat below).

So §3 is now buildable and motivated; §4 (per-run backstop) + nginx per-IP limits
are the cheapest immediate wins. Recommended build order is at the end.
See [`session-persistence.md`](session-persistence.md) for the `get_user_id()`
seam this design keys on.

## The core distinction

"Rate limiting" conflates two different concerns that surface as the same `429`
but live at opposite ends of the stack:

- **Downstream** — the *provider* (OpenAI/Anthropic/Azure) says "slow down". A
  provider-layer concern. **Already handled today** in `chat/providers/`: the
  Anthropic builder owns a custom client with `max_retries` doing transparent
  429/5xx exponential backoff (same place as the cache breakpoints); the OpenAI
  SDK retries 429 by default. The *unbuilt* part is a circuit-breaker /
  Anthropic→Azure fallback — the provider seam (`build_chat_model()`) already
  exists for it. Note: on **free-tier** providers (Gemini/Groq/Mistral) with
  tight per-minute limits, downstream 429s bite *first* — this axis matters more
  than the upstream budget for that setup.
- **Upstream** — *we* protect *our* app from callers (cost, abuse, fairness).
  An edge / API-layer concern. Belongs at nginx (per-IP, coarse) and/or FastAPI
  middleware at the `api` layer (per-identity), keyed on `get_user_id()`. Must
  sit **above** `ChatService` so we reject before spending a token.

```
Cloudflare ─► volumetric/DDoS, bot detection, WAF, per-IP coarse   (network edge)
nginx ──────► per-IP limit_req/limit_conn (CF-Connecting-IP)       (origin edge)   ✓ built
ChatService.dispatch ─► per-user budget gate (get_user_id())       (app layer — upstream) ✓ built
chat/ run_stream ─► per-run UsageLimits backstop                   (orchestration) ✓ built
chat/providers/ ─► 429 backoff (built) / breaker / fallback        (provider — downstream)
```

(The per-user gate lives in `ChatService.dispatch_agent_request`, not FastAPI
middleware — it needs the resolved session + `usage_service`, both already in
scope there, and it must run before the W3 prompt-persist so a rejected turn
stores nothing.)

Neither end is "the LLM layer's job": the provider seam *reacts* to the model's
limits; the API edge *imposes* limits on callers.

## Key insight — requests ≠ tokens

Cloudflare and nginx count **HTTP requests**. They have no idea one `/api/agent`
call cost 40k tokens (long agent run, many tool calls, retry ping-pong). So edge
rate limiting does **not** bound LLM cost. The cost/runaway control lives *inside
the run* via Pydantic AI `UsageLimits`, and any per-user budget is built from
that primitive plus our own accounting.

Two operational caveats noted during discussion:

- **Cloudflare only counts if the origin can't be hit directly.** ✓ **Resolved:**
  the deployment runs behind a **Cloudflare Tunnel** (`docker-compose.prod.yml`) —
  nginx is published on loopback only and cloudflared reaches it over the compose
  network, so the home IP never leaks and CF isn't decorative. Consequence for
  nginx per-IP limits: traffic arrives `CF edge → cloudflared → nginx`, so nginx's
  `$remote_addr` is the tunnel container, NOT the visitor. The limit zones key on
  the real client IP from the `CF-Connecting-IP` header (falling back to
  `$binary_remote_addr` for local/dev) — see `nginx/nginx.conf`.
- A `total_tokens_limit` breach raises `UsageLimitExceeded` and aborts
  **mid-run** → over SSE that's a truncated, half-streamed reply. So it's a
  *backstop*, not the quota mechanism (see §4).

## §3 — Per-user budget: accounting on top of per-run `UsageLimits` — **BUILT**

`UsageLimits` is **per-run**, not per-user — it bounds one run and has no memory
of prior runs. The per-user budget is three pieces, all keyed on `get_user_id()`:

1. **Account** — `UsageService.record()` writes one `app.usage_ledger` row in the
   existing `on_complete` hook from `result.usage`. Note: `result.usage` is a
   **property** in Pydantic AI v2 (no parens — the older `result.usage()` form is
   gone). `RunUsage` exposes `input_tokens` / `output_tokens` / `cache_read_tokens`
   / `cache_write_tokens` / `requests` / `tool_calls`, and `total_tokens`
   (= input + output; cache reads/writes are **excluded** from the total).

   ```python
   async def on_complete(result: AgentRunResult) -> None:
       # ... existing session persistence ...
       await usage_service.record(user_id, result.usage, session_id)
   ```

   **Currency = `total_tokens`** (input + output) — the SAME currency as the
   per-run `total_tokens_limit` backstop, so the gate and the backstop agree. The
   cache columns are stored but NOT budgeted; a future cost-weighted budget (cache
   reads are ~10× cheaper on Anthropic) can be computed without a backfill.

2. **Store** — `app.usage_ledger` (backend-owned; migration
   `0003_app_usage_ledger`, chained after `0002_app_bookmarks`). One append-only
   row per run, `(user_id, created_at DESC)` index; `conversation_id` is nullable +
   `ON DELETE SET NULL` so a user can't reset their budget by deleting threads. A
   **rolling 24h** window: `spent_last_24h` is `SUM(total_tokens) WHERE created_at
   >= now() - interval '24h'` (Postgres server clock — no app-side drift).

3. **Gate — BEFORE the run**, in `ChatService.dispatch_agent_request`, raising a
   domain `QuotaExceededError` (mapped to **429** in `api/agent.py`, alongside the
   existing 404/422/503) — zero tokens spent:

   ```python
   budget = settings.llm_daily_token_budget          # 0 disables the gate
   run_token_cap = _PER_RUN_TOKEN_CAP
   if budget > 0 and usage_service is not None:
       spent = await usage_service.spent_last_24h(user_id)
       remaining = budget - spent
       if remaining < _MIN_RUN_TOKENS:               # reject a doomed tiny run
           raise QuotaExceededError(...)             # → 429
       run_token_cap = min(_PER_RUN_TOKEN_CAP, remaining)
   ```

   The `_MIN_RUN_TOKENS` floor matters: if we let a nearly-exhausted user start a
   run with a tiny `total_tokens_limit`, it would abort mid-stream (the truncated
   SSE the caveat above warns about). Rejecting upfront keeps the failure clean.
   Sized around a **realistic** turn (`30_000`), not a bare-minimal one — a floor
   below a typical turn's `total_tokens` would let users in the band between the
   floor and their real cost pass the gate and then truncate anyway, defeating its
   purpose. Tune against the real `total_tokens` distribution in Phoenix.

Config: `LLM_DAILY_TOKEN_BUDGET` (default 2,000,000; `0` disables the per-user
gate). The per-run backstop (§4) applies regardless.

## §4 — Per-run backstop — **BUILT**

Independent of users, a runaway agent loop (tool-call ping-pong, oversized
context) is a real failure mode **at one user**, so these caps ALWAYS apply.
`adapter.run_stream(...)` accepts `usage_limits=` directly (verified against the
installed adapter) — a one-kwarg add, no refactor:

```python
stream = adapter.run_stream(..., usage_limits=UsageLimits(
    request_limit=_PER_RUN_REQUEST_LIMIT,   # 12
    tool_calls_limit=_PER_RUN_TOOL_CALLS_LIMIT,  # 24
    total_tokens_limit=run_token_cap,       # min(300k, remaining budget)
))
```

Caps sized WELL above a legit complex turn (`locate_place → search_apartments →
apply_*_lens → show_on_map` is ~4 tool calls before retries, plus the deferred
capability's `load_capability`/`search_tools`) so a healthy run never trips them —
they're rails, not a tuning knob. **Any** `UsageLimitExceeded` (backstop OR
budget-edge cap) raises mid-run and truncates the SSE stream — the SAME failure
mode as `total_tokens_limit`, not free of it. So `_with_session_and_lock` catches
`UsageLimitExceeded` in its producer and renders a **graceful** `RUN_ERROR`
("...too long or you've reached your usage limit — try a more specific question")
instead of the generic "problem reaching the model" retry text (which wrongly
implies a transient blip). Revisit the numbers against real Phoenix transcripts if
they ever false-trip.

## nginx per-IP limits — **BUILT**

`nginx/nginx.conf` defines a `limit_req_zone` (`rate=10r/s`) + `limit_conn_zone`
keyed on the real client IP (`CF-Connecting-IP`, see the CF caveat above). Applied:
`/api/agent` — `burst=5` + `limit_conn 4` (an SSE run holds a connection, so the
conn cap is the real guard against parallel-stream floods); `/api/auth` —
`burst=10` (login brute-force guard); `/api/conversations|listings|bookmarks` —
`burst=20`. Breach → 429 (`limit_req_status 429`). Static assets + `/tiles/`
unlimited.

## Rejected / deferred

- **Enforcing the per-user cap via `total_tokens_limit` alone** — rejected: aborts
  mid-stream → truncated SSE reply. The pre-run gate is the real enforcement; the
  per-run limit is only a runaway backstop.
- **Strict per-user concurrency** — two tabs can both pass the pre-check before
  either records usage (`SessionStore.lock` is per-session, not per-user). Minor
  overspend; not worth a per-user guard at this scale. Revisit only if real.
- **Ledgering aborted runs** — `on_complete` (and thus `record()`) fires only on a
  successful run, so a run killed mid-stream by `UsageLimitExceeded` spends real
  provider tokens that never reach the ledger. Same minor-overspend class as the
  concurrency race above, bounded by one run's cap; accepted, not worth capturing
  partial usage off the abort path.
- **Cost-weighted (cache-aware) budgeting** — deferred. We budget flat
  `total_tokens`; the cache columns are stored so this is a pure query change later.
- **Redis token-bucket** — Postgres ledger is fine at this scale.

## Open questions / future

- Window semantics are **rolling 24h** today; fixed daily/monthly would need a
  different store shape.
- Quota tiers once there are distinct user classes (dev/admin vs reviewer vs
  anonymous). Today one budget applies to all authenticated users.
- Surface remaining budget to the UI (chip / header) vs fail-silent. Today it's
  fail-at-limit (429) with no proactive UI signal.
