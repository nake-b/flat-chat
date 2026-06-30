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

## Appear / Disappear / Reappear Logic
State variables in `ChatPane.tsx`:
- `starterOpen`: whether example prompts are visible.
- `starterMessageCount`: counter for initial non-filter user messages.
- `processedUserMessages`: ref to process only newly arrived user turns.

### Rules
For each new user message:
- If message is a **general capabilities prompt**:
  - if prompts are closed, reopen them,
  - reset `starterMessageCount` to `0`,
  - reroll headline + three prompts **only on reopen**.
- Else if message looks like an **apartment filter/search request**:
  - close prompts immediately.
- Else (general non-filter message):
  - increment `starterMessageCount`,
  - close prompts when count reaches `2`.

### Stability requirement
While prompts are already visible, they stay the same selection.
They reroll only when they reappear after being closed.

## Capabilities Trigger UX

### Initial bubble link
- The initial assistant bubble contains markdown link text: `what I can do`.
- A click handler intercepts `#capabilities` links inside assistant messages and auto-sends a capabilities prompt.
- Link styling lives in `index.css` (`.copilotKitAssistantMessage a`): underlined + darker grey.

### Agent policy for capabilities response
In `agent.py`:
- `CAPABILITIES_AT_THE_MOMENT_REPLY` contains the canonical canned response.
- `CAPABILITIES_PROMPT_TRIGGER` is the exact frontend prompt text used by the
  initial-bubble link and starter-card helper.
- `_capabilities_block()` instructs the model:
  - use the canned text exactly for general/open capability questions,
  - answer directly for specific feature questions.

### Backend credit-saving shortcut
In `chat/service.py`, dispatch now checks the latest user message. If it
exactly matches `CAPABILITIES_PROMPT_TRIGGER`:
- no provider model is built,
- no external LLM call is made,
- a local `FunctionModel` yields `CAPABILITIES_AT_THE_MOMENT_REPLY` directly.

The request still goes through normal AG-UI streaming + `on_complete`, so user
and assistant turns are persisted in conversation history and remain reload-safe.

## Prompt Set
- Total starter prompt pool: 20 prompts.
- Tree-specific prompt was removed and replaced (no tree-data dependency).
- Emoji usage reduced (max 1 emoji per prompt; majority have one).

## Notes
- The logic intentionally tracks only `role === "user"` messages with non-empty string content.
- Prompt visibility behavior is frontend-side and independent from backend session state.
