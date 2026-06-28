# Pydantic AI v2 migration

Upgrades the backend from Pydantic AI **1.107** to **2.x**, adopts v2's
**capability** composition primitive for tool binding, and records the
**Harness watch-list** — the v2 features we want but that aren't stable yet.

Status: **implemented** (this PR). Harness package: **not adopted** (watch-list
below). Cost control: **deferred** to the auth / user-service PR.

## Why now

The agent's tool surface is about to grow — pattern-3 frontend command tools
(`pan_map_to`, `expand_card`, `highlight_kiez`, `fit_map_to_results`) plus
distance/travel tools. v2's `capabilities=[...]` is the right home for that
growth, and `defer_loading=True` / ToolSearch is the eventual lever to keep the
cached prompt prefix small as tools multiply. Upgrading while the surface is
still small (3 tools) keeps the migration cheap and attributable.

## What shipped

### Dependencies (`services/backend/pyproject.toml`)
- Replaced the two v1 lines (`pydantic-ai` metapackage + `pydantic-ai-slim[ag-ui]`)
  with a single explicit-extras slim dep:
  **`pydantic-ai-slim[ag-ui,anthropic,openai]>=2.0,<3.0`**.
  v2's core no longer bundles every provider; we declare only what we use
  (`anthropic` → `AnthropicModel`, `openai` → `OpenAIChatModel`/Azure,
  `ag-ui` → `AGUIAdapter`). Re-locking dropped bedrock/groq/mistral/cohere/xai/
  temporal/huggingface and their transitive deps (boto3, tokenizers, …).
- `openinference-instrumentation-pydantic-ai` left at `>=0.1,<0.2` — see
  instrumentation note below.
- This is a **uv workspace**: the authoritative lock is the **root `./uv.lock`**.
  The per-service `services/backend/uv.lock` + `services/ingestion/uv.lock` were
  **deleted** in this PR. They were stale (frozen at 2026-06-15, pre-workspace)
  and unmaintainable: `uv lock` from a member only updates the root lock, never
  the member, so they could never be kept in sync. Local dev + CI already
  resolve against the root lock. CI's `cache-dependency-glob` now points at the
  root `uv.lock`.
  - **Follow-up (not in this PR)**: the Docker builds use `build:
    services/backend/` (context = the service dir), so `uv sync` inside the
    image can't see the workspace root and **re-resolves from `pyproject.toml`
    each build** (non-frozen — it still gets the right versions, verified v2 in
    the built image, but builds aren't reproducible). The proper fix is a
    root-context build that `uv sync --frozen --package`s against the root lock —
    a separate change touching both Dockerfiles + `.dockerignore` + the compose
    build contexts. Worth doing, but out of scope here.

### Capability refactor (`chat/tools.py`, `chat/agent.py`)
- New `ListingsCapability(AbstractCapability[ChatDeps])` returns the existing
  `FunctionToolset` from `get_toolset()`. The three tools (`search_apartments`,
  `open_listing`, `get_result_page`) and the `@toolset.instructions` protocol
  text are **unchanged** — only the Agent wiring moved from `toolsets=[toolset]`
  to `capabilities=[ListingsCapability()]`.
- **Pattern for new tools**: a new agent-callable tool group lands as its OWN
  capability — either another `AbstractCapability` wrapping a toolset, or a
  `Capability(id=…, description=…, instructions=…, defer_loading=True)` with
  `@capability.tool`. `defer_loading=True` keeps it out of the cached prefix
  until the model loads it (the ToolSearch lever). Map/frontend command tools
  and distance tools should follow this.

### Instrumentation (`core/observability.py`)
- v2 defaults the instrumentation data format to **version 5** (aggregated token
  usage on the run span). The current `openinference-instrumentation-pydantic-ai`
  (latest **0.1.x**, no v2-aware release exists yet) reads the **version-4**
  per-request usage attributes. We therefore pin:
  `Agent.instrument_all(InstrumentationSettings(version=4,
  use_aggregated_usage_attribute_names=False))`
  so Phoenix keeps rendering token usage. `tracer_provider=None` → uses the
  global provider we set via `trace.set_tracer_provider`.
- **Drop this pin** once an openinference release supports version 5.

### Tests
- New `tests/unit/test_providers.py` — fills a pre-existing gap (no provider
  tests existed): `build_chat_model()` selection (Anthropic preferred, Azure
  fallback, raises when unconfigured) + the three `anthropic_cache_*`
  breakpoints on the model.
- New `tests/unit/test_capabilities_wiring.py` — asserts the agent advertises
  exactly the three tools through `capabilities=[...]` (guards the refactor).
- `test_retry_suppression.py` (the AG-UI internal-subclass tripwire) passes
  unchanged — verified the override is byte-identical to v2's stock
  `AGUIAdapter.build_event_stream`.

## Breaking changes that did NOT bite us (verified)
- **`openai:`→Responses API**: we construct `OpenAIChatModel`/`AnthropicModel`
  objects directly — no string model prefixes — so unaffected.
- **`AnthropicModelProfile` dataclass→TypedDict**: we don't use the profile;
  caching is via `AnthropicModelSettings`. Unaffected.
- **`FunctionToolset.tool()` now requires `RunContext` first param**: all three
  tools already take `RunContext`. Unaffected.
- **`tool_retries=`**: does NOT exist in v2 — the param is still `retries`, which
  accepts `int | AgentRetries`. `retries={"tools": 3}` is unchanged and valid.
- **`end_strategy` default `early`→`graceful`**: output is plain `str` (no output
  tool), so the "run function tools alongside a successful output tool" change
  has no behavioral effect here. Left unset.

## Harness watch-list (NOT adopted — tracked)

The separate `pydantic-ai-harness` package holds capabilities we want, but the
ones we want are **beta or unmerged PRs**. We track them and revisit when stable:

| Want | Harness capability | Status (at upgrade) |
|---|---|---|
| Context compaction / sliding window | PR #191 | beta/unmerged — most relevant to us (history-authoritative reload replays full history; this bounds prefix growth) |
| Cost / token budgets + guardrails | PR #182 | beta/unmerged — candidate impl for the deferred per-run backstop |
| Tool approval / access control | PR #173 | beta/unmerged — relevant once frontend command tools can act on the UI |
| Memory (key-value) | PR #179 | beta/unmerged |
| Session persistence | PR #176 | beta/unmerged — **we keep our `DbSessionStore`**; it's purpose-fit + tested, the Harness one is generic |

Stable Harness capabilities today (CodeMode, ToolSearch, FileSystem, Shell) are
not what we need. ToolSearch (built into v2 core) is parked until the tool count
actually warrants it — see the `defer_loading` pattern above.

## Deferred to the auth / user-service PR
- **Per-user LLM cost control** AND the **per-run `UsageLimits` backstop** —
  both deferred. The design lives in `llm-rate-limit.md` (currently on the
  frontend-ux branch, to be reconciled onto main with the auth work). When built,
  the Harness cost guardrail (PR #182) is a candidate implementation for the
  per-run backstop.

## Verification
- `uv run ty check src` clean; `uv run pytest tests/unit/` green (125 incl. new).
- Integration tier + app smoke (search → cards/markers, `open_listing` detail,
  no retry-error leak, Phoenix spans at `:6006`) — see the PR's verification.
