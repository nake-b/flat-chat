import { useCopilotAction } from "@copilotkit/react-core";
import { useEffect } from "react";
import { create } from "zustand";

import {
  firstLine,
  THINKING_LABEL,
  TOOL_STATUS,
  type ToolUiSpec,
} from "../state/toolStatus";
import { useUiState } from "./useUiState";

// Cross-component bridge so the "Thinking…" pill (registered separately via
// useCoAgentStateRender in ChatPane) can suppress itself while a tool pill is
// currently executing. Same zustand-store pattern as useHover.
interface ActiveToolStore {
  count: number;
  bump: (delta: 1 | -1) => void;
}

const useActiveToolCount = create<ActiveToolStore>((set) => ({
  count: 0,
  bump: (delta) =>
    set((s) => ({ count: Math.max(0, s.count + delta) })),
}));

type ToolStatus = "inProgress" | "executing" | "complete";

// Single wildcard registration that renders status pills for ALL backend tool
// calls. CopilotKit's validator (in @copilotkit/react-core) treats `name: "*"`
// as a render-only catch-all and never injects it into the LLM's tool list,
// so it stays out of the AG-UI envelope and doesn't trip Pydantic AI's
// RunAgentInput validation.
//
// Adding a new backend tool now requires only one registry entry in
// `state/toolStatus.ts` — no per-tool hook call here.
export function useToolStatusPills() {
  useCopilotAction({
    name: "*",
    render: ({ name, status, args, result }: {
      name: string;
      status: ToolStatus;
      args: unknown;
      result: unknown;
    }) => {
      const spec = TOOL_STATUS[name];
      if (!spec) return <></>;
      return <ToolPill spec={spec} status={status} args={args} result={result} />;
    },
  });
}

function ToolPill({
  spec,
  status,
  args,
  result,
}: {
  spec: ToolUiSpec;
  status: ToolStatus;
  args: unknown;
  result: unknown;
}) {
  const bump = useActiveToolCount((s) => s.bump);

  // CopilotKit's lifecycle for render-only backend tools is `inProgress` (args
  // streaming, repeated as deltas arrive) → `complete` (tool returned). The
  // `executing` status only fires when the action has a frontend handler, so
  // we treat both `inProgress` and `executing` as the "running" phase. During
  // arg streaming, `spec.executing(args)` may fall back to a generic label
  // until the relevant arg arrives, then re-render with the specific one.
  const isRunning = status === "inProgress" || status === "executing";

  useEffect(() => {
    if (!isRunning) return undefined;
    bump(1);
    return () => bump(-1);
  }, [isRunning, bump]);

  if (isRunning) {
    return <ToolStatusInline label={spec.executing(args)} pulse />;
  }
  if (status === "complete") {
    const label = spec.complete
      ? spec.complete(args, result)
      : firstLine(result);
    if (label) return <ToolStatusInline label={label} pulse={false} />;
  }
  return <></>;
}

// Rendered by ChatPane via useCoAgentStateRender. Visible only when the agent
// is running AND no tool pill is currently executing — i.e. the LLM is
// reasoning between (or before / after) tool calls.
export function ThinkingSlot() {
  const { running } = useUiState();
  const activeCount = useActiveToolCount((s) => s.count);
  if (!running || activeCount > 0) return null;
  return <ToolStatusInline label={THINKING_LABEL} pulse />;
}

// Reusable pill UI shared by tool renders and the Thinking slot.
export function ToolStatusInline({
  label,
  pulse,
}: {
  label: string;
  pulse: boolean;
}) {
  return (
    <div
      className="fc-status-line my-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-soft"
      role="status"
      aria-live="polite"
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full bg-red ${
          pulse ? "animate-pulse" : ""
        }`}
        aria-hidden
      />
      {label}
    </div>
  );
}
