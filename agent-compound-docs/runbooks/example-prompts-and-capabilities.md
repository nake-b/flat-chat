# Example Prompts + Capabilities UX (June 2026)

## Scope
This document describes the frontend and agent behavior added for:
- starter/example prompts in chat,
- clickable "what I can do" in the initial assistant bubble,
- the canned `CAPABILITIES_AT_THE_MOMENT_REPLY` behavior.

Primary files:
- `services/frontend/src/components/ChatPane.tsx`
- `services/frontend/src/state/starterPrompts.ts` (+ `.test.ts`) — the tagged prompt pool + `pickStratified`
- `services/frontend/src/index.css`
- `services/backend/src/flat_chat/chat/agent.py`

## Starter Prompt UX

### Rendering and placement
- Example prompts render as compact **pills** (rounded-full chips, `flex flex-wrap`)
  in the left chat pane under `+ New chat / Sign out`, below a random headline.
- Each pill shows a short **`label`** (e.g. "500 m around Alexanderplatz"); the full
  sentence (`prompt`) is what gets sent on click. Decoupling label from payload is
  what keeps the pills small while still sending a rich prompt.
- **Three** pills are shown, sampled from **distinct capability categories** so they
  showcase different things the app can do (see Prompt Set).

### Prompt click behavior (send immediately)
`sendPrompt(prompt)` in `ChatPane.tsx` uses CopilotKit's programmatic send API:

```ts
const { sendMessage } = useCopilotChatInternal();
sendMessage({ id: crypto.randomUUID(), role: "user", content: prompt }, { followUp: true });
```

`followUp: true` runs the agent after appending the message. The prompt flows
through `POST /api/agent` exactly like a typed message — persisted, reload-safe,
and it dismisses the starter cards (the derived `starterOpen` sees the new user
turn). Both the starter cards and the `#capabilities` link go through this one
helper. No textarea scraping, no `requestAnimationFrame` submit retries.

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

### Semantic-fallback honesty policy
Some requests describe an attribute with **no structured filter** (dog-friendly,
student-friendly, "arty", "loft vibe"). These only engage `search_apartments`'
free-text `query`, which **ranks** by semantic similarity — it does not hard-filter.
`_semantic_fallback_block()` in `agent.py` instructs the model, whenever it routes
such a wish into `query`, to tell the user in one short sentence that it couldn't
filter by that attribute and instead ranked by closeness to their words (so they
should check the listing text). This is truthful in practice — embeddings are
populated (the `world.listings_embeddings` table has rows), so semantic ranking
actually runs rather than silently degrading to recency. Structured filters need no
such caveat. It's an LLM-behavior policy (cached static instruction), covered by the
conversation smoke harness, not a deterministic unit test.

## Prompt Set
The pool lives in `services/frontend/src/state/starterPrompts.ts` as an array of
concrete objects — `{ category, label, prompt }` — not bare strings. Frontend-owned
(these are UI/onboarding copy; matches the "frontend owns appearance" split).

- **`category`** (`budget | place | transit | family | nature | calm | map | health
  | semantic`) drives `pickStratified(pool, 3)`: shuffle categories, take one prompt
  from each of 3 distinct categories (fill from leftovers if fewer categories exist).
  So the visible trio always spans different capabilities — no three near-duplicates.
- **`label`** is the short pill text; **`prompt`** is the full sentence sent.
- **Curated to real capabilities.** Every prompt maps to a capability that actually
  exists (verified against `search/schemas.py`, `geo_filters.py`, the `locate_place`
  gazetteer, and `listings/types.py`). Removed the disability-parking prompt (no such
  filter — it's detail-only) and the generic "near a University" prompt (no university
  category; only *specific* named places resolve). Reworded "as close as possible to a
  lake" → "close to a lake" and "biggest parks?" → "next to a big park" (no proximity/
  size sort exists).
- **`semantic` category = intentional soft-attribute demos.** "student-friendly" (and
  the dog vibe under `nature`) have no structured filter; the agent routes them to the
  free-text `query` (semantic ranking) and is instructed to say so — see below.

`pickStratified` is unit-tested in `starterPrompts.test.ts` (distinct categories,
count, pool-membership, leftover-fill).

## Notes
- The logic intentionally tracks only `role === "user"` messages with non-empty string content.
- Prompt visibility behavior is frontend-side and independent from backend session state.

## Design rationale & standard-practice references
_Revised July 2026 after PR #36 review — this section records WHY the feature
is shaped the way it is, and which parts follow (or deliberately diverge from)
established chatbot UX practice._

### Starter prompts ARE the standard pattern ✅
Clickable example/suggestion prompts are the well-established way to surface a
chatbot's capabilities. Nielsen Norman Group documents these as **"prompt
controls"** — task examples rendered as buttons/cards that teach users what the
bot can do, increase feature discoverability, and lower interaction cost so
people don't have to ask "can you…". The canonical implementations are Claude's
"New in Claude" conversation starters and ChatGPT's suggestion chips. Our
left-pane cards match this pattern directly, so we kept them.
- NN/g — *Prompt Controls in GenAI Chatbots*: https://www.nngroup.com/articles/prompt-controls-genai/
- NN/g — *CARE: A Structure for Crafting AI Prompts*: https://www.nngroup.com/articles/careful-prompts/

### Visibility: empty-thread-only (rejected: counter + keyword classifier)
The prompts show only while the thread is empty, then dismiss for good. This
replaced an earlier scheme that counted "non-filter" turns, classified each
message with hardcoded English keyword lists (`isApartmentFilterPrompt` /
`isGeneralCapabilitiesPrompt`), and rerolled/reopened the cards. That scheme was
rejected because the keyword lists were a maintenance liability (English-only,
easy to misclassify) and reappearing cards mid-conversation is more surprising
than helpful. NN/g's guidance treats prompt controls primarily as an *onboarding
/ empty-state* affordance — which is exactly "show when there's nothing yet".

### Capabilities reply: system-prompt guidance, NOT a verbatim canned string
There are three common ways to answer "what can you do":
1. **Model answers from a system-prompt capabilities section** — flexible, adapts
   to the exact question. ← **we use this** (`_capabilities_block()`).
2. **Static client-side panel** — zero tokens, most robust, but not part of the
   conversation and invisible to the agent on later turns.
3. **Hardcoded canned reply matched by string** — the most fragile and least
   common; a fixed string can't adapt to a scoped question.

The PR originally shipped (3): the agent was told to reply with EXACTLY the
canned text, and `chat/service.py` additionally short-circuited an exact
frontend/backend text match to a local `FunctionModel` so no LLM call was made.
Review feedback pushed us to (1):
- **"Don't force EXACTLY the same text."** A user asking "which data can you
  access" should get a focused answer about geo-context data, not the whole
  recital. So `CAPABILITIES_AT_THE_MOMENT_REPLY` is now a **reference summary**
  the model adapts (source of truth for *what's* available; the model owns
  phrasing and scope).
- **"Is there a more robust signal than comparing text?"** Yes — but we removed
  the shortcut instead of hardening it. Exact string equality across a
  frontend + backend constant is brittle (any punctuation drift silently
  disables it), and it *conflicts* with an adaptive reply (a fixed
  `FunctionModel` string can't focus on a sub-topic). If a credit-saving
  shortcut is ever wanted again, the robust form is a **structured signal**
  (a message id / metadata flag set by the frontend link), never a text match.
- **The shortcut wasn't even saving much.** `build_chat_model()` is
  `@lru_cache(maxsize=1)` — the model is built once per process, not per
  message — so the shortcut only skipped one (cheap) LLM call, not any model
  construction. Not worth a dual-maintained constant and a whole code path.

### Programmatic send — resolved ✅
An earlier version **DOM-scraped** CopilotKit's composer to submit starter /
capabilities prompts (`querySelector` the textarea, dispatch a synthetic `input`
event, then `requestAnimationFrame`-retry the submit button) — brittle against
CopilotKit's internal class names and the send-button enable timing. This now
uses the framework's programmatic send API: `useCopilotChatInternal().sendMessage`
(see "Prompt click behavior" above). The public `useCopilotChat()` omits
`sendMessage`, so the internal hook is required — it's the same hook already used
for `messages` here and for `setMessages` in `ConversationRecovery.tsx`, and
needs no `publicApiKey`.
