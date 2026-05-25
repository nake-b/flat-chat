# Chat runtime & streaming

Decided 2026-05-25.

## Context

The frontend talks to the agent over a thin HTTP surface, but the load-bearing details are not obvious from reading any single file:

- Why there are two endpoints (`POST /api/conversations` + `POST /api/agent`) when the AG-UI envelope already carries a `thread_id` and one endpoint could lazily allocate the session.
- How SSE is actually "enabled" — there's no `@app.sse(...)` decorator; it falls out of four cooperating pieces (Pydantic AI's `agent.iter()`, `AGUIAdapter.run_stream`, Starlette's `StreamingResponse`, and nginx's `proxy_buffering off`).
- Why `_with_session_and_lock` is a generator *inside* the streaming response instead of an `async with` at the call site.
- Why `InMemorySessionStore.lock()` raises on unknown session ids instead of lazily creating a lock.
- Why state events (`STATE_SNAPSHOT`) are emitted explicitly from tools via `ToolReturn(metadata=[...])` rather than auto-derived from `deps.state` mutations.
- Why `ChatService` translates `ValidationError → 422`, `RuntimeError → 503`, `SessionNotFoundError → 404` — and why this matters for a streaming endpoint specifically.

Any of these would read like a candidate for "simplification" to a fresh contributor. They're load-bearing. This document captures the contract end-to-end so the next change to `chat/service.py`, `chat/sessions.py`, `chat/tools.py`, or `nginx.conf` is made with the full picture.

Sister docs:
- [`frontend-stack.md`](./frontend-stack.md) — what `UiState` is, how the frontend mirrors it, status-pill lifecycle, layout.
- [`backend-architecture.md`](./backend-architecture.md) — domain layering and dependency flow.
- [`llm-tool-result-design.md`](./llm-tool-result-design.md) — `ResultSet`'s prose/CSV/detail contract.

## Decision

### Endpoint design

Three routes, one streaming, two REST:

| Route | Method | Purpose |
|---|---|---|
| `POST /api/conversations` | POST | Allocate a session. Returned `id` doubles as the AG-UI `thread_id`. |
| `POST /api/agent` | POST + SSE response | Run the agent on the latest message. Streams text deltas, tool-call lifecycle, and state snapshots back. |
| `GET /api/conversations/{id}/messages` | GET | History reload after page refresh. Read-only projection of `session.message_history` → `(role, content)` pairs. |

The lazy alternative — a single `POST /api/agent` that creates the session if the `thread_id` is unknown — was rejected:

- **ID provenance.** The frontend needs the `thread_id` at `<CopilotKit>` mount time to wire `HttpAgent({ threadId })`. Lazy allocation would force the React tree to either generate UUIDs client-side or wait for the first POST response — both ugly.
- **Lifecycle separation.** Conflating "allocate a thread" with "send a message" means the first POST to `/api/agent` has implicit side effects (creates state) while subsequent POSTs don't. Surprising.
- **History reload needs a resource URL anyway.** `GET /api/conversations/{id}/messages` exists for page-refresh recovery; once that GET exists, the matching POST for creation is the obvious shape, and `SessionStore.create()` was already going to exist.

The cost is one extra round-trip on page load (~10ms locally). When the in-memory store is replaced by a DB-backed one (post-MVP), `/api/conversations` becomes the natural place to also list/rename/delete conversations.

### The AG-UI envelope

Every `POST /api/agent` request body is an AG-UI `RunAgentInput`:

```json
{
  "threadId": "uuid-from-/api/conversations",
  "runId": "...",
  "messages": [...full history...],
  "state": { "results": [...], "active_id": "..." },
  "tools": [...]
}
```

Three things to notice:

1. **The full message history is sent every turn.** The wire is stateless from the server's perspective — `threadId` correlates back to a session record only so we can persist + serve the GET reload endpoint. Pydantic AI's agent run is driven from the envelope's `messages`, not from server-side memory. Why this is fine: AG-UI was designed for replayable, stateless servers; sending history is cheap relative to model latency; and it removes the "did the server remember?" failure mode that bedevils stateful chat protocols.
2. **`state` mirrors the frontend's view of `UiState`.** When the user clicks a card, `setState({ active_id })` updates the local React mirror; that diff lands in the envelope's `state` on the *next* POST. The agent sees what the user is looking at without a side-channel. Frontend → backend writes piggyback on the next turn — no separate write endpoint needed.
3. **`tools` declares frontend-callable actions** (the AG-UI Generative-UI Pattern-3 hook). We don't use this today — our tools all run on the backend — but the field is reserved.

### How SSE is enabled in the backend

There's no `@app.sse(...)` decorator. SSE falls out of four cooperating layers:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Pydantic AI                                               │
│    agent.iter(...) yields BaseEvent objects as the LLM      │
│    streams tokens and tool calls.                            │
└────────────────────┬────────────────────────────────────────┘
                     │ async iterator of BaseEvent
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. AGUIAdapter (pydantic_ai.ui.ag_ui)                       │
│    adapter.run_stream(...) wraps the agent run and yields   │
│    AG-UI lifecycle events: RUN_STARTED,                      │
│    TEXT_MESSAGE_CONTENT, TOOL_CALL_START/ARGS/END/RESULT,    │
│    STATE_SNAPSHOT, RUN_FINISHED.                             │
│                                                              │
│    adapter.streaming_response(iterator) wraps the iterator   │
│    in a Starlette StreamingResponse with                     │
│    `Content-Type: text/event-stream`, serializes each event  │
│    as `data: {...json...}\n\n`, sets keep-alive headers.    │
└────────────────────┬────────────────────────────────────────┘
                     │ StreamingResponse (lazy generator body)
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Starlette / FastAPI                                       │
│    The HTTP response object accepts an async iterator as     │
│    its body and flushes each yielded chunk to the wire.      │
│    No special framework switch — just the SSE Content-Type   │
│    on a long-lived response.                                 │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP chunked transfer over keep-alive
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. nginx (`location /api/agent`)                             │
│    `proxy_buffering off` lets chunks through immediately.    │
│    `proxy_set_header Accept-Encoding ""` prevents any        │
│    intermediate gzipping (buffered compression would defeat  │
│    proxy_buffering off).                                     │
│    `proxy_read_timeout 3600s` because agents can take a      │
│    while.                                                    │
│    `X-Accel-Buffering: no` header set in api/agent.py is     │
│    belt-and-braces for any other nginx-compatible proxy.     │
└─────────────────────────────────────────────────────────────┘
```

Forget any one of these and the user sees nothing until the agent finishes. The nginx layer is the one that bites: with default `proxy_buffering on`, the entire SSE response is buffered into memory and flushed at the end, which looks identical to "the backend hung."

The `_RUNTIME_INFO` GET probes (`/api/agent/info` + `/api/agent/threads`) on the same router are unrelated to streaming — they exist so CopilotKit's boot-time runtime discovery doesn't spam a red banner. See the docstring on `run_agent` in `api/agent.py` for the full reasoning.

### `_with_session_and_lock` lives *inside* the generator

In `chat/service.py`:

```python
async def _with_session_and_lock(stream, session_id, lock):
    async with lock:
        with using_session(session_id):
            async for event in stream:
                yield event
```

The naive version puts `async with lock:` at the call site of `dispatch_agent_request`. That doesn't work: **Starlette consumes the inner iterator after the handler function returns.** If the lock is acquired at the call site, it releases *before any event flows*, which means two concurrent same-thread runs race over the same `ChatSession` (e.g. a rapid double-send).

The wrapper exists to extend the lifetime of both the lock and the `using_session(session_id)` Phoenix tracing context across the *consumption* of the stream, not just its creation. Acquiring inside the generator is the natural way to do this — the `async with` lives in the coroutine that Starlette drives.

Same reasoning applies to `using_session`: Phoenix needs the session id active while spans are being emitted, which happens chunk-by-chunk as the agent runs, not at construction time.

### Session store: in-memory, LRU-capped, per-session async lock

`chat/sessions.py` defines two things behind the `SessionStore` Protocol:

1. **`_sessions: dict[str, ChatSession]`** with `_MAX_SESSIONS = 100`. On `create()`, if the dict is full, the oldest entry (by `created_at`) is evicted. This is a tiny LRU bounded by allocation order — not access order — which is fine for MVP because session lifecycle is dominated by tab-close, not idle eviction.

2. **`_locks: dict[str, asyncio.Lock]`** — per-session locks lazily created. **Crucially, `lock(session_id)` raises `SessionNotFoundError` if the session doesn't exist** rather than auto-creating a lock. The naive shape — `defaultdict(asyncio.Lock)` — would let any caller grow `_locks` unboundedly by sending requests with spoofed `thread_id` values. With `lock()` strict, an attacker who guesses no valid session ids cannot enlarge memory beyond `_MAX_SESSIONS` lock entries.

The `SessionStore` Protocol exists so the in-memory implementation can be swapped for a DB-backed one without touching `ChatService`. The future DB store will share the surface area but use Postgres advisory locks instead of asyncio locks (and probably skip the LRU cap entirely).

### Exception translation in `ChatService`

`ChatService.dispatch_agent_request` translates three failure modes into HTTP status codes before the response starts streaming:

| Exception | HTTP code | Cause |
|---|---|---|
| `pydantic.ValidationError` from `AGUIAdapter.from_request` | 422 Unprocessable Entity | Malformed envelope. CopilotKit's runtime-discovery probe (`{"method":"info"}`) lands here legitimately — we let it 422 instead of short-circuiting it (see `api/agent.py` docstring). |
| `SessionNotFoundError` from `store.get` / `store.lock` | 404 Not Found | Spoofed or expired `thread_id`. |
| `RuntimeError` from `build_chat_model()` | 503 Service Unavailable | No LLM provider key is set. This is a deployment issue, not a request issue, so 503 is more honest than 500. |

Why this matters for a streaming endpoint: once any byte of the SSE response has been written, you can no longer return an HTTP status code. The translations all happen *before* `adapter.streaming_response(...)` is returned. Errors that arise mid-stream (LLM API outage, model timeout) surface as in-band AG-UI error events, not HTTP failures — that's an AG-UI protocol concern, not something this layer handles.

### State emission is opt-in

This is the single most counter-intuitive thing in the runtime.

Pydantic AI's `AGUIAdapter` does **not** auto-emit `STATE_SNAPSHOT` or `STATE_DELTA` events when `deps.state` mutates. Confirmed by reading `pydantic_ai/ui/ag_ui/_event_stream.py` — the imports list every event type except the state ones. If you mutate `ctx.deps.state.results = [...]` inside a tool and return a plain string, the frontend's `useCoAgent` mirror **will not update**.

Tools must opt in by returning a `ToolReturn` with the state event in `metadata`:

```python
from pydantic_ai import ToolReturn
from ag_ui.core.events import EventType, StateSnapshotEvent

def _return_with_state(return_value: str, ui_state: UiState) -> ToolReturn:
    return ToolReturn(
        return_value=return_value,
        metadata=[StateSnapshotEvent(
            type=EventType.STATE_SNAPSHOT,
            snapshot=ui_state.model_dump(),
        )],
    )

@toolset.tool
async def search_apartments(ctx: RunContext[ChatDeps], ...) -> ToolReturn:
    # ... mutate ctx.deps.state and ctx.deps.session.result_set ...
    return _return_with_state(
        return_value=ctx.deps.session.result_set.summary(),
        ui_state=ctx.deps.state,
    )
```

The adapter yields any `BaseEvent` placed in `ToolReturn.metadata` into the SSE stream alongside the regular `TOOL_CALL_RESULT` event. The helper lives in `chat/tools.py`; every tool that mutates `state` must use it.

Why this design (rather than monkey-patching the adapter to auto-emit):

- It's explicit at the call site — you can tell from a tool's `return` whether it pushes UI state.
- It avoids diff machinery on the server (the adapter would otherwise need to deep-compare `deps.state` before/after each tool call).
- `STATE_SNAPSHOT` is a full snapshot, not a diff — cheap to construct, idempotent on the wire, and the frontend doesn't need JSON Patch replay logic.

### Persistence on completion

After the agent run completes (every turn, every time), `on_complete(result: AgentRunResult)` is invoked by `AGUIAdapter`. We use it to rebuild and save:

```python
async def on_complete(result: AgentRunResult) -> None:
    session.message_history = list(result.all_messages())
    session.ui_state = deps.state
    self.store.save(session)
```

We **rebuild** message history from `result.all_messages()` rather than incrementally appending. Reason: AG-UI sends the full thread in every envelope, so the run result *is* the authoritative history for this turn. Appending the new tail of `result.new_messages()` would also work but introduces an indexing bug-class (what if the envelope diverges from server memory?). Rebuilding is simpler and matches the wire's stateless nature.

`session.ui_state = deps.state` is a reference assignment — `deps.state` is the same object the `AGUIAdapter` set from the envelope and that tools mutated during the run.

### Is this industry-standard?

Yes. The pattern — client POSTs, server streams structured events on the response body — has converged across the agentic-chat ecosystem:

- **AG-UI Protocol** (CopilotKit + Pydantic AI) — what we use.
- **Vercel AI SDK** `useChat` — "UI Message Stream Protocol" over POST + SSE.
- **OpenAI Assistants API streaming** — POST + SSE with delta events.
- **Anthropic Messages API streaming** — POST + SSE, text deltas + tool_use blocks.
- **LangGraph / LangServe** — POST + SSE for streaming endpoints.

The differentiator AG-UI adds on top of this baseline is **first-class typed shared state** (`STATE_SNAPSHOT` / `STATE_DELTA` via JSON Patch). Sibling React components (map, cards, chat) subscribe to slices of the agent's authoritative state without bespoke wire protocols. Most other ecosystems either re-fetch on completion or hand-roll websocket sync.

SSE wins over WebSockets for this shape of traffic:
- Works through standard HTTP proxies and load balancers (nginx, Cloudflare) with no special config beyond `proxy_buffering off`.
- Unidirectional server→client *after the POST* fits a turn-based UX exactly — the server does most of the work per turn.
- Easier to reason about and cheaper to scale (no persistent bidi connection).
- Frontend → backend signals piggyback on the next POST's envelope `state` field — no separate write channel needed.

WebSockets would be necessary if the backend needed to push to a client without a recent POST (server-initiated alerts, multi-user cursors). Agentic chat doesn't have those needs.

The Python ecosystem is ~18 months behind TypeScript's polish here. A year ago you'd be rolling SSE + JSON Patch yourself. `pydantic-ai-slim[ag-ui]`'s `AGUIAdapter` is the cleanest Python option today.

## Rejected alternatives

- **Single `/api/agent` lazily creating sessions on first POST.** Loses ID provenance at React mount time, first POST has implicit side effects (creates state) while subsequent POSTs don't. See §Endpoint design.
- **Auto-emitted `STATE_SNAPSHOT` on `deps.state` mutation.** Would require monkey-patching `AGUIAdapter` or wrapping it. Explicit `_return_with_state` is one line per tool and keeps the wire contract visible at the call site.
- **`defaultdict(asyncio.Lock)` for the per-session lock map.** Lets any caller grow `_locks` unboundedly by sending spoofed `thread_id` values — a trivial DoS. `lock()` raises on unknown ids instead.
- **WebSockets** for the agent channel. Overkill for half-duplex turn-based chat; harder nginx config; no benefit over SSE for our traffic shape. SSE is the right primitive when "server pushes only after the client asked" describes the protocol.
- **Persisting message-by-message inside the SSE generator.** Would have to handle partial writes, generator cancellation, and reconcile with the next envelope's full history. AG-UI's stateless wire makes the `on_complete` rebuild simpler and the run result authoritative.
- **Acquiring the session lock at the `dispatch_agent_request` call site.** Releases before any event flows because Starlette consumes the iterator after the handler returns. Has to live inside the generator. Same for Phoenix's `using_session(session_id)`.
- **Short-circuiting CopilotKit's `{"method":"info"}` probe in `run_agent`.** Tested — it makes CopilotKit route messages via the runtime client, bypassing the `agents__unsafe_dev_only` HttpAgent we wired on the React side. We let the probe 422 instead; CopilotKit logs a warning and falls back correctly. See `api/agent.py` docstring.

## Consequences

- **Adding a new tool that mutates `UiState`** requires `_return_with_state(...)` — not just `return "ok"`. Forgetting it is silent: the chat reply is fine, the frontend mirror just doesn't update.
- **Adding a new SSE-streaming endpoint** must (a) be registered under `/api/agent` in `nginx.conf` or get its own `location` block with the same SSE settings, and (b) set `X-Accel-Buffering: no` on the response.
- **Adding a new `SessionStore` failure mode** must map to an HTTPException in `ChatService.dispatch_agent_request` *before* the streaming response starts. Once bytes are on the wire, the status code is fixed.
- **Replacing `InMemorySessionStore` with a DB-backed store** keeps the Protocol surface but should preserve `lock()` raising on unknown ids and the LRU cap (or its DB equivalent — TTL on session rows). The `on_complete` persistence hook is the migration point.
- **The architecture diagram** (`architecture.drawio`) should reflect the four-layer SSE pipeline if it doesn't already. See [`architecture-diagram.md`](./architecture-diagram.md) for the regeneration workflow.

## See also

- [`frontend-stack.md`](./frontend-stack.md) — `UiState` mirror, status-pill lifecycle, layout, chat ↔ map shared-state loop.
- [`backend-architecture.md`](./backend-architecture.md) — domain layering, `chat/`, `search/`, `api/` boundaries.
- [`llm-tool-result-design.md`](./llm-tool-result-design.md) — `ResultSet`'s prose/CSV/detail contract (the LLM-facing parallel projection of `UiState`).
- `services/backend/src/flat_chat/api/agent.py` — runtime-info probe handling.
- `services/backend/src/flat_chat/chat/service.py` — `dispatch_agent_request`, `_with_session_and_lock`, `on_complete`.
- `services/backend/src/flat_chat/chat/sessions.py` — `InMemorySessionStore`, LRU cap, `lock()`.
- `services/backend/src/flat_chat/chat/tools.py` — `_return_with_state` helper.
- `nginx/nginx.conf` — `location /api/agent` block.
