# LLM Tool Result Design

Principles for the shape and size of data we hand back to the agent from a tool call. Applies to `search_apartments`, `get_result_page`, `get_result_details`, and any future tool that returns a list of things.

## The reference

**[MCP Pagination Patterns — Handling Large Result Sets Without Blowing Your Context (ChatForest)](https://chatforest.com/guides/mcp-pagination-patterns/)**

The single most useful piece on this topic. Read it before designing any tool that returns more than a single record. Distilled below.

Supporting reading:
- [Building LLM-Friendly MCP Tools in RubyMine — JetBrains Blog](https://blog.jetbrains.com/ruby/2026/02/rubymine-mcp-and-the-rails-toolset/)
- [Tools — Model Context Protocol spec](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

## The principles we apply

### 1. Never return everything
A tool that returns 10,000 rows blows the context, drops focus, and turns into a slow, expensive bad answer. Cap server-side. Typical defaults: 10–25 per page, max 100.

In our code: `search_apartments` returns top 5 in the summary, `get_result_page` defaults to 10 per page.

### 2. Always tell the model what was trimmed
Every paginated response includes `total`, the current visible range, and what's missing. Without this the LLM either pretends it has the full set, or wastes calls fishing for more. The footer is non-negotiable:

```
Found 42 listings, most recent first.
Showing 1–5:
  ...
37 more available. To explore further:
  • get_result_page(page=2)   — next 10 results
  • get_result_details(indices=[N, ...])  — full info for specific listings
  • search_apartments(...)    — refine with new filters
```

This footer is what makes #6, #7, etc. *reachable*. The model can't paginate to results it doesn't know exist.

### 3. Tell the truth about ordering
"Top 5" implies ranking. If the user didn't pick a sort and there's no query, the order is `created_at DESC` — that's "5 most recent," not "top 5." The label must reflect the actual sort:

| `sort_by`         | Label                              |
|-------------------|------------------------------------|
| `relevance` (with query) | sorted by relevance to your query |
| `price`           | sorted by lowest warm rent          |
| `area`            | sorted by largest area              |
| default / no sort | most recent first                   |

### 4. Format matches purpose
- **Prose** for narrative summaries — the model has to *think* about counts, ranges, recommendations. Prose helps.
- **CSV/compact rows** for bulky data — `get_result_page` returns many listings whose only purpose is to be displayed. CSV saves 29–60% tokens vs JSON per ChatForest.
- **Prose** for `get_result_details` — these are the listings the user is deciding on; the LLM may reason about them, so readability beats compactness.

### 5. Strip the fields you don't need
Return only what the agent actually uses in the workflow. Don't ship internal IDs, raw lat/lon, embeddings, debug timings to the LLM — they cost tokens for no reason.

### 6. Bake "what to do next" into the response
JetBrains' line: *"Without telling the LLM what it should do differently, it has to figure it out by itself, which can result in additional unnecessary tool calls."* Our footer is exactly that — explicit next-step menu, with the tool names and arg shapes already written out.

## Where this lives in our code

These principles are implemented on `ResultSet` (in `chat/state.py`), which is the single source of truth for formatting any result-set view shown to the LLM:

- `ResultSet.summary(top_n=5)` — prose, with footer
- `ResultSet.page(page, page_size=10)` — CSV body, with footer
- `ResultSet.detail(indices)` — prose, full fields
- `ResultSet.describe_for_instructions()` — one-line state injected into agent instructions every turn
- `ResultSet.order_label()` — the truthful sort description
- `ResultSet._format_row()` — single shared formatter
