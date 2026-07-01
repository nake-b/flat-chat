# Example Prompts + Capabilities UX (June 2026)

## Scope
This document describes the frontend and agent behavior added for:
- starter/example prompts in chat,
- clickable "what I can do" in the initial assistant bubble,
- the canned `CAPABILITIES_AT_THE_MOMENT_REPLY` behavior.

Primary files:
- `services/frontend/src/components/ChatPane.tsx`
- `services/frontend/src/index.css`
- `services/backend/src/flat_chat/chat/agent.py`

## Starter Prompt UX

### Rendering and placement
- Example prompts render in the left chat pane under `+ New chat / Sign out`.
- Three prompts are shown at once, randomly selected.
- A random headline is shown above the prompt boxes.

### Prompt click behavior (send immediately)
`submitPromptToComposer(prompt)` in `ChatPane.tsx` performs:
1. Programmatically set composer textarea value.
2. Dispatch an `input` event so React/CopilotKit state updates.
3. Submit immediately:
   - first via `textarea.form.requestSubmit()` when available,
   - fallback by clicking submit button,
   - with short `requestAnimationFrame` retries to handle delayed button enable.

Result: selecting an example prompt sends directly to the agent (not just filling input).

## Visibility Logic
Starter prompts show **only while the thread is empty**. As soon as the user
sends any message they're dismissed and do not come back for that thread.

- `starterOpen` is DERIVED (a `useMemo`), not stored: it's `true` when no
  `role === "user"` message with non-empty string content exists yet.
- `starterHeadline` and the three `starterPrompts` are picked once on mount and
  stay stable — no reroll, no reappear.

This intentionally replaced an earlier scheme (a `starterMessageCount` counter,
a `processedUserMessages` ref, keyword-based filter/capabilities classification,
and reroll-on-reopen). Empty-thread-only is simpler and less surprising, and it
drops the hardcoded English keyword lists that were a maintenance liability.

## Capabilities Trigger UX

### Initial bubble link
- The initial assistant bubble contains markdown link text: `what I can do`.
- A click handler intercepts `#capabilities` links inside assistant messages and auto-sends a capabilities prompt (`CAPABILITIES_PROMPT` in `ChatPane.tsx`).
- The click sends a normal user turn through `POST /api/agent` — it is persisted in history and visible to the agent on later turns, same as any typed message.
- Link styling lives in `index.css` (`.copilotKitAssistantMessage a`): underlined + darker grey.

### Agent policy for capabilities response
In `agent.py`:
- `CAPABILITIES_AT_THE_MOMENT_REPLY` is a **reference summary** of what's
  actually available right now (source of truth — the model must not invent
  capabilities beyond it).
- `_capabilities_block()` instructs the model to use that summary as guidance
  and **adapt** it to the question:
  - open question ("what can you do") → cover the whole picture,
  - scoped question ("which data can you access") → lead with and focus on the
    relevant part (e.g. just geo-context data), drop the rest,
  - specific feature / concrete operation → answer directly instead of reciting
    the summary.
- The reply is NOT verbatim — the model may reword for concision and a natural
  tone, keeping the honest caveat about the current database snapshot.

There is **no backend text-match shortcut**. An earlier version short-circuited
an exact-text match to a canned `FunctionModel` reply to save a credit; it was
removed because (a) exact string matching across a frontend + backend constant
is brittle, and (b) a fixed string can't adapt to scoped questions the way the
guidance policy above does. Every capabilities question now runs a normal
(cheap) agent turn. Note `build_chat_model()` is `@lru_cache`d, so the model is
constructed once per process, not per message — the shortcut never saved model
construction, only one LLM call.

## Prompt Set
- Total starter prompt pool: 20 prompts.
- Tree-specific prompt was removed and replaced (no tree-data dependency).
- Emoji usage reduced (max 1 emoji per prompt; majority have one).

## Notes
- The logic intentionally tracks only `role === "user"` messages with non-empty string content.
- Prompt visibility behavior is frontend-side and independent from backend session state.
