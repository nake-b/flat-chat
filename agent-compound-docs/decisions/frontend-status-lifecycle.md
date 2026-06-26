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

## What was rejected

- **Keying on a richer set of booleans inline in the pill.** That's what produced the bug —
  adding a third boolean (`!streaming`) inline would work but leaves the next person to
  rediscover the implicit state machine. Naming the phase makes the invariant explicit.
- **Driving Thinking off AG-UI `THINKING_*` events directly.** Pydantic AI only emits those
  when the model produces reasoning parts, which we don't rely on. The derived phase works
  regardless of whether the model streams thinking tokens.
