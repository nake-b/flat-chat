# Frontend status lifecycle — one explicit agent phase

How the chat thread decides *which* status indicator to show at any moment, why it's
derived from a single mutually-exclusive **phase** rather than ad-hoc booleans, and how
that splits responsibility with the per-tool copy registry.

## The symptom

The "Thinking…" pill showed **while the assistant was writing its answer** — it sat on top
of the streaming prose until the run ended. (Claude Code analogy: you see "Thinking" before
the answer starts, then it disappears the instant text begins — never on top of it.)

## Root cause

The Thinking pill keyed on two signals:

```ts
const { running } = useUiState();              // useCoAgent
const activeCount = useActiveToolCount(...);    // >0 while a tool executes
const shouldShow = !!running && activeCount === 0;
```

`running` is CopilotKit's **whole-run** flag — `true` from `RUN_STARTED` to `RUN_FINISHED`,
which includes the text-streaming phase. So during answer generation no tool is active,
`activeCount === 0`, and `shouldShow` stayed `true`. The indicator was really a
`running && no-tool` heuristic that conflated three distinct phases of a run into one, and
it was wrong precisely when text streamed with no tool around.

## The model — one phase, derived

AG-UI already defines a structured event lifecycle (`RUN_STARTED/FINISHED`,
`TEXT_MESSAGE_START/CONTENT/END`, `TOOL_CALL_START/…/RESULT`, `THINKING_START/END`). We
collapse the signals we can observe into a single phase in `hooks/useAgentPhase.ts`:

| phase | condition | indicator |
|---|---|---|
| `idle` | `!running` | nothing |
| `tool` | `running` & a tool is executing | the per-tool pill (registry) |
| `streaming` | `running` & latest msg is an assistant text msg with content | nothing — the answer IS the indicator |
| `reasoning` | `running` & none of the above | the rotating "Thinking…" pill |

Inputs, all already in the app:
- `running` — `useUiState()` (`useCoAgent`).
- tool-execution count — the `useActiveToolCount` zustand store in `useToolStatus.tsx`
  (exported for this).
- assistant-text-streaming — `useCopilotChatInternal().messages`; the last message has
  `role === "assistant"` and non-empty string `content`.

Exactly one phase is active at a time, so at most one indicator renders — this structurally
kills the "two indicators fighting" class of bugs.

> **Version coupling — `useCopilotChatInternal`.** The streaming check reads the *internal*
> CopilotKit hook `useCopilotChatInternal().messages` and depends on the message shape
> `{id, role, content}` (it's the same internal hook `ConversationRecovery` already uses for
> `setMessages`, so the dependency is accepted project-wide). It's pinned at `@copilotkit/*
> ^1.10.0`; a CopilotKit upgrade must re-verify that `messages` is still an array of
> `{role, content}`. `useAgentPhase.test.tsx` asserts the `streaming` branch against that exact
> shape, so a breaking reshape fails the test loudly rather than silently resurrecting the
> "Thinking on top of the streaming answer" bug.

## Division of labour

- **`useAgentPhase`** owns the *run-level* split: Thinking vs streaming vs tool vs idle.
- **`state/toolStatus.ts`** (the registry) owns *per-tool* copy: `executing` /
  `executingRotating` (during) and `complete` (on finish; return `""` to show nothing). A
  new tool — e.g. `locate_place` ("finding location of TU Berlin") — is **one registry
  entry** with its own during/after labels; the phase machinery is untouched.

This is the same split as [`ag-ui-tool-retry-suppression.md`](./ag-ui-tool-retry-suppression.md):
decisions that depend on a *type* or *lifecycle position* are made where that information
lives (backend for the retry type; the phase hook for the run position), and the rendered
copy stays dumb.

## Tool finishes persist via the transcript (issue #22)

A second symptom in the same family: within one turn the agent may run several
`search_apartments` calls (search → 0 hits → broaden → search again), so the chat stacked a
wall of identical "No apartments found…" pills.

The principle is the same one this whole doc is about — **make the decision where the
information lives** — applied to persistence: the message transcript is the single source of
truth. A tool "finish" is a real tool-call result *message*; "Thinking" is an ephemeral
phase indicator and is not a message. So finishes persist (and re-render on reload) for
free, and Thinking does not.

How it works:

- **Render copy** stays in `state/toolStatus.ts` (the SSOT for pill text). The search finish
  is `search_apartments.complete → formatSearchBreadcrumb(parseSearchCount(result))`. The
  wildcard `useCopilotAction({name:"*"})` render shows it.
- **Live and reload use the same render path.** On reload `ConversationRecovery` calls
  `setMessages` with the FULL transcript (`GET /messages` → `AGUIAdapter.dump_messages`,
  `by_alias` camelCase). CopilotKit's `useLazyToolRenderer` re-fires the wildcard render for
  restored `toolCalls` + matching `role:"tool"` results — so the finish reappears with zero
  extra frontend state.
- **The "let-through" policy is the backend's, in both paths** (cf.
  `ag-ui-tool-retry-suppression.md`): the live AG-UI event stream
  (`chat/service.py:_FlatChatEventStream.transform_stream`) and the reload serializer
  (`api/chat.py:_serialize_history`) each drop `reasoning`/`activity` (Thinking ephemeral) and
  blank retry results (`error` set). A blanked tool result has empty content → the wildcard
  render shows nothing.
- **CopilotKit can't clear a pill.** Once a tool pill shows a result it can't be replaced or
  cleared — a second `TOOL_CALL_RESULT` for the same `toolCallId` is ignored (verified in a
  browser). So a single status line that *mutates* ("No apartments found" → Thinking →
  "Searching") is impossible with the per-call pills; finishes only coexist. The collapse
  works by HOLDING a search result and choosing whether to emit it empty or with content —
  never by clearing one already shown.
- **Live** (`transform_stream`): hold each search result; when the NEXT search starts, emit
  the held (superseded) one EMPTY (its only result event → pill resolves to nothing, so no
  lingering or doubled "Searching…"). Flush the held result WITH content at the answer text /
  run end. Net: a silent broadening turn (0→0→48) shows ONE finish; a turn that narrates
  between searches shows each result once, interleaved with its narration — never two
  spinners.
- **Reload** (`_serialize_history`): collapses to the LAST search finish per turn — the
  settled summary. A narrated turn that showed 3→6→23 live restores as just "Found 23". Live
  is the working narration; reload is the clean record.

### What was rejected (and why it took a browser to learn)

First attempt derived the count by scanning the assistant renderer's `messages` prop for the
turn's last `search_apartments` result, surfaced via a zustand store + a custom
`AssistantMessage` wrapper that froze counts by message id. Two things only a real browser
revealed:
- CopilotKit's react-ui **strips tool calls/results out of the `messages` prop** handed to
  renderers — it carries only user/assistant TEXT. So the message-scan approach had no data.
- Even when rendered, a bare child of `.copilotKitMessagesContainer` is force-hidden by
  `> div:not(.copilotKitMessage){display:none!important}`.

The store/wrapper also never survived reload (in-memory, populated only by the live tool
render). All of it was deleted in favour of the transcript-SSOT approach above. The contract
is pinned by `backend/tests/integration/test_conversations_api.py` (GET /messages includes
tool calls/results, collapses multi-search, blanks retries, drops thinking) +
`test_llm_context.py` (summary first-line shape) + `frontend/src/state/searchBreadcrumb.test.ts`
(parser) + `toolStatus.test.ts` (finish copy).

**Lesson:** for CopilotKit render integration, verify the runtime prop shape in a browser —
the TypeScript types (`AssistantMessageProps.messages: Message[]`) advertised tool messages
the runtime doesn't actually deliver to renderers.

## What was rejected

- **Keying on a richer set of booleans inline in the pill.** That's what produced the bug —
  adding a third boolean (`!streaming`) inline would work but leaves the next person to
  rediscover the implicit state machine. Naming the phase makes the invariant explicit.
- **Driving Thinking off AG-UI `THINKING_*` events directly.** Pydantic AI only emits those
  when the model produces reasoning parts, which we don't rely on. The derived phase works
  regardless of whether the model streams thinking tokens.
