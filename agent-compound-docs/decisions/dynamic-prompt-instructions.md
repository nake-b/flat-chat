# Dynamic vs cached prompt instructions

Decided 2026-06-15 during the search-perf refactor.

## Context

Pydantic AI composes the agent's system prompt in three layers:

1. **Agent `instructions=`** — role-level prose (who you are, what UI
   you're talking to, honesty rules). Static byte sequence; evaluated
   once at import.
2. **`@agent.instructions`** — dynamic per-turn instructions, evaluated
   on every run.
3. **`@toolset.instructions`** — tool-protocol guidance, co-located with
   the toolset.

Anthropic prompt caching (verified ~5600 cached prefix tokens per turn
with `cache_instructions=True` + `cache_tool_definitions=True`) requires
the cached prefix to be a **stable byte sequence** — any dynamic content
in the cached layers breaks the cache.

## Decision

The rule for where each kind of instruction lives:

| Kind | Goes in | Cached? |
|---|---|---|
| Role / persona / honesty / neutrality | Agent `instructions=` | Yes |
| Tool-protocol (when to use which tool, phrase map) | `@toolset.instructions` | Yes |
| **State-dependent** ("don't reopen the active listing #3") | `@agent.instructions` → `build_dynamic_state_prompt` | No |
| **Per-turn state snapshot** (current search, total, filters, focus) | `@agent.instructions` → `build_dynamic_state_prompt` | No |

The crucial bit: **state-dependent rules MUST go in the dynamic layer**,
even if they look like "tool protocol" prose. Putting them in
`@toolset.instructions` would either be wrong content (you can't
hardcode "don't open #3" when the index varies) or kill the cache.

## Concrete examples

In `chat/llm_context.py:build_dynamic_state_prompt`:

```xml
<current_state>
  <total>487</total>
  <loaded>500</loaded>
  <order>sorted by relevance to your query</order>
  <filters>{"districts": ["Kreuzberg"], "transit": {"modes": ["u_bahn"]}}</filters>
</current_state>

<user_focus>
  The user is viewing listing #3 in the detail panel.
  Do NOT call open_listing for index 3 — its full details are below.

  <active_listing>
    [tier-3 prose: nearby transit, schools, kitas, parks, landmarks, noise profile, Bezirk/Ortsteil, ...]
  </active_listing>
</user_focus>
```

The `<user_focus>` block disappears entirely when no listing is open.
The XML structure is stable (Claude attends to tagged blocks better
than inline prose); only the inner values change turn to turn.

In `chat/tools/core.py:tool_protocol_instructions` (static, cached):

```
<tool_protocol>
There is ONE active result set per conversation. Listings are referenced
by 1-based indices...
</tool_protocol>

<phrase_map>
  - "near U-Bahn" → transit: {modes: ["u_bahn"]}
  - "inside the ring" / "city center" → inside_ring: true
  - "near the Tiergarten" → locate_place("Tiergarten") → near_place_ref
  ...
</phrase_map>
```

These are *general* protocol rules — they don't change turn to turn,
so they cache cleanly.

## The "don't re-open the active listing" rule

When the user has a listing open, the LLM also has its tier-3 detail in
context (via `<user_focus>` → `<active_listing>`). Calling
`open_listing` again for the same index would be redundant — the data's
already there.

The instruction lives in `<user_focus>` directly co-located with the
data:

> Do NOT call open_listing for index {N} — its full details are below.

Co-location is the point. The LLM sees the data and the rule together,
making the rule unambiguous. The instruction only exists when there's a
focus listing, so it can't pollute turns where nothing is open.

## Sources

- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Pydantic AI Agent Instructions](https://ai.pydantic.dev/agents/#instructions)
- [Pydantic AI System Prompt Composition](https://ai.pydantic.dev/agents/#system-prompts)
