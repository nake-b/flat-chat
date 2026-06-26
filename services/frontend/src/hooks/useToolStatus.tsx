import { useCopilotAction } from "@copilotkit/react-core";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { create } from "zustand";

import { AnimatedDots, RotatingWord } from "../components/StatusAnimation";
import {
  firstLine,
  THINKING_LABEL,
  THINKING_VERBS,
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
  // Current SessionState so rotating labels can derive progress (e.g. the
  // pagination percent from total_results).
  const { state } = useUiState();

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
    const rot = spec.executingRotating?.(args, state);
    const label = rot ? (
      <>
        <RotatingWord words={rot.verbs} />
        {rot.suffix}
        {rot.trailing}
      </>
    ) : (
      spec.executing(args)
    );
    return <ToolStatusInline label={label} pulse />;
  }
  if (status === "complete") {
    const label = spec.complete
      ? spec.complete(args, result, state)
      : firstLine(result);
    // Empty completion (e.g. a suppressed tool-retry whose result content the
    // backend blanked) renders nothing — the failed attempt quietly vanishes.
    if (label) return <ToolStatusInline label={label} pulse={false} />;
  }
  return <></>;
}

// Thinking pill that injects itself as the LAST child of
// `.copilotKitMessagesContainer`, so it sits in the same vertical rhythm as
// tool pills — directly below the most recent message. We *cannot* use
// CopilotKit's `useCoAgentStateRender`: its claim-bridge anchors the render
// to a message id that can be stale, parking the pill above an old assistant
// bubble. By owning a portal slot we ourselves keep at the end of the
// container (via MutationObserver), positioning is deterministic.
//
// Hook variant — call it inside ChatPane. The hook returns JSX (a portal),
// so callers must include `{useThinkingPillInStream()}` in their tree.
export function useThinkingPillInStream(): React.ReactNode {
  const { running } = useUiState();
  const activeCount = useActiveToolCount((s) => s.count);
  const shouldShow = !!running && activeCount === 0;

  const [slot, setSlot] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!shouldShow) {
      setSlot(null);
      return undefined;
    }

    // CopilotChat mounts asynchronously — poll briefly until the container
    // exists, then attach. 50ms × up to 20 tries (~1s) covers cold mount.
    let cancelled = false;
    let attachInterval: ReturnType<typeof setInterval> | undefined;
    let observer: MutationObserver | undefined;
    let createdSlot: HTMLDivElement | undefined;

    const attach = () => {
      const target = document.querySelector(
        ".copilotKitMessagesContainer",
      ) as HTMLElement | null;
      if (!target) return false;
      if (cancelled) return true;

      const node = document.createElement("div");
      node.setAttribute("data-fc-thinking-slot", "");
      target.appendChild(node);
      createdSlot = node;
      setSlot(node);

      // Keep our slot at the very end whenever CopilotKit appends/removes
      // children. Cheap — the container has at most ~tens of children.
      observer = new MutationObserver(() => {
        if (target.lastElementChild !== node) {
          target.appendChild(node);
        }
      });
      observer.observe(target, { childList: true });
      return true;
    };

    if (!attach()) {
      let tries = 0;
      attachInterval = setInterval(() => {
        if (cancelled || attach() || ++tries >= 20) {
          if (attachInterval) clearInterval(attachInterval);
        }
      }, 50);
    }

    return () => {
      cancelled = true;
      if (attachInterval) clearInterval(attachInterval);
      observer?.disconnect();
      createdSlot?.remove();
      setSlot(null);
    };
  }, [shouldShow]);

  if (!slot || !shouldShow) return null;
  return createPortal(
    <ToolStatusInline
      label={<RotatingWord words={THINKING_VERBS} />}
      ariaLabel={THINKING_LABEL}
      pulse
    />,
    slot,
  );
}

// Reusable pill UI shared by tool renders and the Thinking slot. When `pulse`
// (running), animated dots are appended to the label so the line breathes;
// `label` may be a rotating element, so it's a ReactNode. `ariaLabel` gives
// screen readers a stable phrase when the visible label is animated.
export function ToolStatusInline({
  label,
  pulse,
  ariaLabel,
}: {
  label: React.ReactNode;
  pulse: boolean;
  ariaLabel?: string;
}) {
  return (
    <div
      className="fc-status-line my-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-soft"
      role="status"
      aria-live="polite"
      aria-label={ariaLabel}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full bg-red ${
          pulse ? "animate-pulse" : ""
        }`}
        aria-hidden
      />
      {label}
      {pulse && <AnimatedDots />}
    </div>
  );
}
