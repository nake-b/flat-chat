# SessionState — canonical in-memory representation of the active conversation

Decided 2026-06-15 during the search-perf refactor.

## Context

Before the refactor, the agent's "active conversation" lived in *three*
parallel places:

- `ChatSession.result_set: LlmResultSetView` — pandas DataFrame +
  `SearchParams`, used for LLM-facing prose
- `ChatSession.ui_state: UiState` — `list[UiApartment]` + `active_id` +
  `active_listing_context`, used for the frontend mirror
- The pandas DataFrame inside `LlmResultSetView` — separate copy of the
  same data the UiApartments were built from

Three representations, two copies of tier-2 data (~500 KB at 500
listings), two layers to keep in sync.

## Decision

Collapse into **one** in-memory object: `SessionState`.

> **Superseded shape (2026-06-15).** The original `results:
> list[UiApartment]` field — one flat list of all tier-2 cards — was
> replaced by the tiered shape below during the tiered-result-set
> refactor. The single-list rationale that follows the code block is
> kept for history but no longer describes the wire format. See
> **Tiered result set** below.

```python
class SessionState(BaseModel):
    # The applied search (question)
    search_params: SearchParams | None
    total_results: int                            # real count (len markers, or COUNT(*) at cap)

    # The materialized result set (answer)
    result_markers: list[Marker]                  # tier 1, EVERY match (≤ MARKER_CAP=5000)
    preview_cards: list[ListingCard]              # tier 2, top PREVIEW_N=10 (hot)

    # The active selection (focus)
    active_id: UUID | None
    active_listing_detail: ListingDetail | None   # tier 3
```

`SessionState` is the agent's working memory, the frontend mirror, AND
the source for LLM prose all at once. `LlmResultSetView` survives as a
thin formatter that reads `state` directly — no separate DataFrame, no
separate params cache.

## Tiered result set (supersedes the single `results` list)

The flat `results: list[UiApartment]` made the state grow O(matches × tier-2)
— ~260 KB at 500 listings, worse at thousands. We split it by tier so
the heavy part (full cards) scales independently of the markers:

- **`result_markers`** — EVERY match as a thin tier-1 marker
  `{id, lat, lng, price_warm}`, hard-capped server-side at
  `MARKER_CAP`=5000. This is BOTH the map source and the ordered result
  set (the answer). It is serialized **columnar** on the wire
  (`{ids, lats, lngs, prices}`) via a Pydantic `@field_serializer` and
  decoded back by a paired `@field_validator`. The pair is symmetric:
  the AG-UI envelope echoes state back from the frontend, and
  `_extract_incoming_state` re-validates it through the same validator,
  so a round-trip is loss-free.
- **`preview_cards`** — the top `PREVIEW_N`=10 full `ListingCard`s. Hot
  for the LLM (so it can talk about the head of the list without a
  fetch) and for the card strip's first paint.
- **`total_results`** — now a real count: `len(result_markers)`, or
  `COUNT(*)` when the 5000 cap binds.

`SearchService.search()` returns `(markers, preview_cards, total)` —
there is no per-search `limit` arg anymore; markers are hard-capped
server-side. The shared tier-2 projection lives in
`listings/projection.py`, reused by both the preview build and
`ListingService.get_cards(ids)`.

**On-demand hydration.** Cards beyond the preview window are fetched by
id via a batch read — `GET /api/listings?ids=…&view=card` → `ListingCard[]`
in request order (≤100 ids, cacheable). See `agent-vs-http-data-flow.md`.

**Ownership.** `SessionState` is *the answer* — the backend owns it. The
frontend owns its own *view-state*, including the cache of hydrated
tier-2 cards (`cardCache` zustand store) that fills in as the user
scrolls past the preview. The hydrated-card cache is deliberately NOT in
`SessionState`: it's derivable from `result_markers` + the batch
endpoint and would only bloat the wire.

## Naming

Industry convention (AWS Bedrock `SessionState`, LangGraph "state",
Pydantic AI's `StateDeps`) uses `state` as the noun for the persistent
container; "snapshot" is reserved for the *event* that pushes it down
the wire (AG-UI's `STATE_SNAPSHOT`). Old `UiState` name was misleading
because it implies the UI owns it — the backend owns it, the frontend
mirrors it. Renamed `UiState` → `SessionState`.

## Why `active_listing_detail` lives in state

The earlier version of the plan moved tier-3 detail OUT of state and
forced the frontend to HTTP-fetch on click. We reversed that decision
mid-design: ~10 KB for ONE active listing is not "heavy" (the AG-UI
guidance against heavy payloads in state was about 5 MB-scale payloads,
not 10 KB per listing).

Keeping `active_listing_detail` in state means **the agent always knows
what the user is looking at, in full detail**, without an extra tool
call. The LLM can answer follow-up questions ("is this area safe?")
directly from the `<user_focus>` block in `build_dynamic_state_prompt`.

The HTTP endpoint `GET /api/listings/{id}` is still the primary fetch
path (frontend uses it on card click); state is the *cache* for the
LLM. Frontend writes the fetched detail back to state via two-way AG-UI
sync so the backend has it on the next turn.

## Why we kept the snapshot (and dropped the DataFrame)

The original intent of caching the DataFrame was "if the user adds a
filter, we can refine in memory without re-running SQL". That intent
was never implemented — every refinement re-runs `search_apartments`
with merged params, hitting Postgres again.

Re-running gold-table searches is cheap (~50ms) so the in-memory
refinement isn't worth building yet. But the *snapshot* — having
`result_markers` + `preview_cards` in memory — is genuinely useful:

- Pagination tool `get_result_page` is 0 DB hits over the marker list
- LLM-facing summary regeneration is 0 DB hits
- The agent can answer "of these, which are quietest?" over the preview
  cards without a fresh SQL query — just reads the snapshot

If refinement-without-re-query becomes a real need (e.g. we ship a
client-side filter UI), the path is: integrate pandas into
`SessionState` and add a `state.refine(params)` method that filters the
snapshot in memory. The snapshot already exists; only the refinement
plumbing would be new. **TODO documented in `CLAUDE.md`.**

## What changed in `LlmResultSetView`

Before:
```python
@dataclass
class LlmResultSetView:
    df: pd.DataFrame
    params: SearchParams
    notes: list[str]
```

After:
```python
@dataclass
class LlmResultSetView:
    state: SessionState
```

Same prose methods (`summary`, `page`, `detail`), zero data ownership.
It's a formatter, not a cache.

## Sources

- [AWS Bedrock SessionState API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_SessionState.html)
- [AWS Bedrock — Control agent session context](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-session-state.html)
- [Pydantic AI — AG-UI Integration](https://ai.pydantic.dev/ui/ag-ui/)
- [Agent State Management Guide 2026 (AgentMemo)](https://agentmemo.ai/blog/agent-state-management-guide.html)
- [LangGraph State with Pydantic BaseModel](https://medium.com/fundamentals-of-artificial-intelligence/langgraph-state-with-pydantic-basemodel-023a2158ab00)
