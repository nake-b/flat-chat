# LLM rate limiting & per-user usage budgets

Where rate limiting belongs in the stack, and how to build a **per-user LLM
usage budget** on top of Pydantic AI's per-run `UsageLimits`.

Status: **proposed / deferred** — not built. The per-run backstop (§4) is the
only piece worth adding before auth exists; the per-user accounting (§3) is a
companion to the `app` schema / auth work (session-persistence stage 2/3). See
[`session-persistence.md`](session-persistence.md) for the `get_user_id()` seam
this design keys on.

## The core distinction

"Rate limiting" conflates two different concerns that surface as the same `429`
but live at opposite ends of the stack:

- **Downstream** — the *provider* (Anthropic/Azure) says "slow down". A
  provider-layer concern. Handled by the provider SDK's exponential backoff,
  configured inside each builder in `chat/providers/` (same place as the
  Anthropic cache breakpoints). A circuit-breaker / Anthropic→Azure fallback
  would slot in here if overload ever becomes real — the provider seam
  (`build_chat_model()`) already exists for the fallback.
- **Upstream** — *we* protect *our* app from callers (cost, abuse, fairness).
  An edge / API-layer concern. Belongs at nginx (per-IP, coarse) and/or FastAPI
  middleware at the `api` layer (per-identity), keyed on `get_user_id()`. Must
  sit **above** `ChatService` so we reject before spending a token.

```
Cloudflare ─► volumetric/DDoS, bot detection, WAF, per-IP coarse   (network edge)
nginx ──────► per-IP limit_req, connection caps, SSE handling      (origin edge)
api/ middleware ─► per-identity quotas (get_user_id())             (app layer — upstream)
chat/ ──────► orchestration only
chat/providers/ ─► 429 backoff / breaker / fallback               (provider — downstream)
```

Neither end is "the LLM layer's job": the provider seam *reacts* to the model's
limits; the API edge *imposes* limits on callers.

## Key insight — requests ≠ tokens

Cloudflare and nginx count **HTTP requests**. They have no idea one `/api/agent`
call cost 40k tokens (long agent run, many tool calls, retry ping-pong). So edge
rate limiting does **not** bound LLM cost. The cost/runaway control lives *inside
the run* via Pydantic AI `UsageLimits`, and any per-user budget is built from
that primitive plus our own accounting.

Two operational caveats noted during discussion:

- **Cloudflare only counts if the origin can't be hit directly.** If the origin
  IP leaks, attackers skip CF entirely (WAF, DDoS, limiting all evaporate) and
  nginx becomes the real edge. Lock the origin to CF IP ranges (firewall
  allowlist) or use a Cloudflare Tunnel — otherwise CF is decorative.
- A `total_tokens_limit` breach raises `UsageLimitExceeded` and aborts
  **mid-run** → over SSE that's a truncated, half-streamed reply. So it's a
  *backstop*, not the quota mechanism (see §4).

## §3 — Per-user budget: accounting on top of per-run `UsageLimits`

`UsageLimits` is **per-run**, not per-user — it bounds one `agent.run()` and has
no memory of prior runs. A per-user budget is three pieces, all keyed on the
existing `get_user_id()` seam:

1. **Account** — read `result.usage()` in the existing `on_complete` hook (it
   already receives the `AgentRunResult` at SSE-stream end for persistence) and
   add the token count to a per-user running total.

   ```python
   async def persist_session(result: AgentRunResult) -> None:
       usage = result.usage()
       await usage_store.add(user_id, usage.total_tokens)
       # ... existing session persistence ...
   ```

2. **Store** — a counter keyed by user + time window. Home is the backend-owned
   `app` schema (a timestamped ledger row per run, or a windowed counter).
   Redis with a TTL is the classic token-bucket option if fast resets matter;
   Postgres is fine at this scale. Windowing (per day/month) is pure accounting —
   Pydantic AI has no opinion on it.

3. **Gate — check the budget BEFORE the run**, in
   `ChatService.dispatch_agent_request`, rejecting cleanly before spending tokens:

   ```python
   spent = await usage_store.spent_this_window(user_id)
   if spent >= QUOTA:
       return quota_exceeded_response()   # zero tokens spent
   remaining = QUOTA - spent
   result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(
       total_tokens_limit=min(PER_RUN_CAP, remaining),  # backstop only
   ))
   ```

The real quota enforcement is the **pre-run gate** (clean rejection); the per-run
limit is only a safety backstop against a single runaway run.

## §4 — Per-run backstop (the one thing worth doing now)

Independent of users, a runaway agent loop (tool-call ping-pong, oversized
context) is a real failure mode **at one user**. Bound every run:

```python
result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(
    request_limit=10, tool_calls_limit=8, total_tokens_limit=...,
))
```

This protects against our own agent, not the internet — so it applies regardless
of auth or user count.

## Rejected / deferred

- **Per-user limiting now** — moot. One hardcoded `get_user_id()` dummy means no
  second user to be unfair to. Build §3 alongside the `app` schema + auth, not
  before.
- **Enforcing the per-user cap via `total_tokens_limit` alone** — rejected:
  aborts mid-stream → truncated SSE reply. Gate upfront instead.
- **Strict per-user concurrency** — two tabs can both pass the pre-check before
  either records usage (`SessionStore.lock` is per-session, not per-user). Minor
  overspend; not worth a per-user guard at this scale. Revisit only if real.

## Open questions for when this is built

- Window semantics: rolling vs fixed (daily/monthly) — affects store choice.
- Quota tiers once real users/auth exist (anonymous vs authenticated).
- Whether to surface remaining budget to the UI (chip / header), or fail silent.
