# Suppressing Tool-Retry Errors in the AG-UI Stream

Why a Pydantic AI tool **validation/retry error** can leak into the chat UI as raw text, why
it's *our* config that surfaces it (not an upstream bug), and the decision to neutralize it
at the backend by type rather than string-match it on the frontend.

## The symptom

When the LLM emits arguments that fail a tool's schema (or a tool raises `ModelRetry`), the
chat thread briefly showed an ugly block like:

```
2 validation errors:
```json
[ { "type": "int_parsing", "loc": ["max_price"], ... } ]
```

Fix the errors and try again.
```

The agent then *retries* the call with corrected args and succeeds — so the error is a
transient internal correction the user should never see. (Claude Code analogy: you see "doing
web search", never "web search error" mid-retry.)

## Root cause — traced end to end

1. **Pydantic AI** turns a failed validation into a `RetryPromptPart`
   (`pydantic_ai/_output.py`), whose `model_response()` renders the `"N validation errors… Fix
   the errors and try again."` string (`pydantic_ai/messages.py`).
2. **The AG-UI adapter** flattens it. `AGUIEventStream._handle_tool_result`
   (`pydantic_ai/ui/ag_ui/_event_stream.py:251`) **unconditionally** emits a
   `ToolCallResultEvent` with that string as `content`:
   ```python
   async def _handle_tool_result(self, result: ToolReturnPart | RetryPromptPart):
       if isinstance(result, RetryPromptPart):
           output = result.model_response()      # the error dump
       else:
           output = _tool_return_content(result)
       yield ToolCallResultEvent(..., content=output)   # always emitted
   ```
   There is **no config flag** to suppress it.
3. **CopilotKit** delivers that `content` to our wildcard `useCopilotAction({name:"*"})` render
   as the tool's `complete` `result`, and our status pill prints it via
   `firstLine(result)` (`services/frontend/src/hooks/useToolStatus.tsx`).

## Why nobody patched this upstream (the important part)

CopilotKit renders tool **results invisibly by default** — *"without a `*` renderer, tool
calls are invisible; the user only sees the assistant's final text summary."* So in the
overwhelmingly common setup, the retry result is streamed and silently dropped by the
frontend. Nobody sees it → no upstream pressure to add a suppression flag.

**We are the rare configuration:** AG-UI adapter (forwards every event) **+** a wildcard pill
that echoes `result`. That specific combination is what surfaces the error. The leak is a
property of our opt-in status pills, not a framework defect.

The structural signal that says "this is a retry, not a real result" — the **type**
`RetryPromptPart` vs `ToolReturnPart` — exists **only in the backend**. By the time the event
reaches the frontend it is a flat `content` string with no error flag. Therefore any
frontend-only fix is reduced to sniffing the error text (`"Fix the errors and try again."`),
which is fragile and was rejected.

## Where the framework *expects* you to filter

Pydantic AI's blessed event-filtering seam is **one layer below** the AG-UI adapter:
`event_stream_handler` + `Agent.run_stream_events()`, where you iterate the *native*
`AgentStreamEvent`s yourself (a failed validation is a distinct `tool-input-error` event) and
choose which to forward — the documented idiom is *"parse each event, check if it's a display
event, yield to frontend or keep for debugging."* Using that means **not** using the
`AGUIAdapter` convenience and hand-rolling the SSE loop — a large rewrite of
`chat/service.py`. We chose not to pay that.

## Decision

`error → don't show. no error → parse and show.` Decide error/no-error **in the backend**
(where the type lives); keep the frontend dumb. Success results — including the liked
"Found 57 apartments" completion breadcrumb — keep flowing through `firstLine(result)`
**unchanged**. Only the error case is neutralized, at the source.

**Implementation** — a ~15-line subclass of the AG-UI event stream, wired in via the public
`build_event_stream()` override point (`pydantic_ai/ui/ag_ui/_adapter.py:244`):

```python
class _QuietRetryEventStream(AGUIEventStream[ChatDeps, str]):
    async def _handle_tool_result(self, result):
        if isinstance(result, RetryPromptPart):
            yield ToolCallResultEvent(
                message_id=self.new_message_id(), type=EventType.TOOL_CALL_RESULT,
                role="tool", tool_call_id=result.tool_call_id, content="",   # empty, not the error
            )
            return
        async for e in super()._handle_tool_result(result):
            yield e

class _FlatChatAGUIAdapter(AGUIAdapter[ChatDeps, str]):
    def build_event_stream(self):
        return _QuietRetryEventStream(
            self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version)
```

`chat/service.py` constructs `_FlatChatAGUIAdapter` instead of the bare `AGUIAdapter`.

**Empty content, not a dropped event** — emitting an empty-`content` result lets CopilotKit
close out that tool call cleanly (no stuck/pulsing pill). The frontend already renders nothing
for a falsy `complete` label (`firstLine("") === ""`), so **no frontend change is needed**: the
failed attempt vanishes; the successful retry shows "Found 57 apartments" as before.

## Rejected alternatives

- **Frontend string-match** (`isRetryError(result)` sniffing `"Fix the errors and try
  again."`). Fragile — couples UI to an English error sentence that upstream can reword; only
  catches this one error shape.
- **Frontend "pills never echo tool text"** (derive the count from `SessionState.total_results`
  instead of the result string). Robust against *all* raw-text leaks, but changes the liked
  breadcrumb behavior and introduces a stale-count edge for the transient failed attempt. Not
  worth it once the backend neutralizes the error at the source.
- **`event_stream_handler` / `run_stream_events()`** (the upstream-blessed filter). Upgrade-safe
  (public API only) but a large rewrite — abandons the adapter we rely on.

## Cost / risk

`_handle_tool_result` is a **private** Pydantic AI method, so a future upgrade could rename or
reshape it. Mitigation: a backend unit test drives `_QuietRetryEventStream` with a
`RetryPromptPart` and asserts the emitted event has empty `content` — it fails loudly if the
override stops hooking (`services/backend/tests/unit/test_retry_suppression.py`). Revisit the
`event_stream_handler` path only if this proves brittle across upgrades.

## Not affected — history corruption (#3197)

[pydantic-ai #3197](https://github.com/pydantic/pydantic-ai/issues/3197) reports that a
`ModelRetry` can leave *streamed* history with a tool-call missing its tool-response,
corrupting the next turn for frontend-authoritative apps. We are **backend-authoritative**:
`on_complete` rebuilds history from `result.all_messages()` (`chat/service.py`), and
`_serialize_history` already drops retry parts on reload (`api/chat.py`). So our persisted
history stays valid regardless. No action needed.

## Sources

- [Pydantic AI — AG-UI integration](https://pydantic.dev/docs/ai/integrations/ui/ag-ui/)
- [Pydantic AI — UI overview](https://pydantic.dev/docs/ai/integrations/ui/overview/)
- [Pydantic AI — agent / `event_stream_handler` API](https://ai.pydantic.dev/api/agent/)
- [CopilotKit — tool-call rendering (invisible by default)](https://docs.copilotkit.ai/google-adk/generative-ui/tool-rendering)
- [pydantic-ai #3197 — ModelRetry + AG-UI history corruption](https://github.com/pydantic/pydantic-ai/issues/3197)
