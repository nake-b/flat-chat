import { useCopilotChatInternal } from "@copilotkit/react-core";

import { useActiveToolCount } from "./useToolStatus";
import { useUiState } from "./useSessionState";

// The single lifecycle phase of the agent run, derived from the three
// orthogonal signals CopilotKit / AG-UI expose. Exactly one phase is active at
// a time, so at most one status indicator renders — this is what keeps the
// "Thinking" pill from fighting the tool pills or sitting on top of a streaming
// answer.
//
//   idle       — no run in flight; show nothing
//   tool       — a backend tool is executing; the per-tool pill owns the line
//   streaming  — the assistant is writing its answer; the answer IS the
//                indicator, so show nothing
//   reasoning  — run is active but nothing is on screen yet (pre-tool, or the
//                gap between a tool finishing and the next step) → "Thinking…"
//
// Why this exists: `running` (useCoAgent) is true for the WHOLE run, including
// the text-streaming phase. Keying the Thinking pill on `running && no-tool`
// therefore showed "Thinking" on top of the streaming answer. Splitting out an
// explicit `streaming` phase fixes that. See
// agent-compound-docs/decisions/frontend-status-lifecycle.md.
export type AgentPhase = "idle" | "reasoning" | "tool" | "streaming";

export function useAgentPhase(): AgentPhase {
  const { running } = useUiState();
  const activeTools = useActiveToolCount((s) => s.count);
  const { messages } = useCopilotChatInternal();

  if (!running) return "idle";
  if (activeTools > 0) return "tool";

  // The answer is streaming once the latest message is an assistant text
  // message with non-empty content (AG-UI messages are {id, role, content};
  // a mid-flight tool call leaves content empty, which is the `tool` branch
  // above or the reasoning gap below).
  const last = messages[messages.length - 1] as
    | { role?: string; content?: unknown }
    | undefined;
  const streaming =
    last?.role === "assistant" &&
    typeof last.content === "string" &&
    last.content.trim().length > 0;

  return streaming ? "streaming" : "reasoning";
}
