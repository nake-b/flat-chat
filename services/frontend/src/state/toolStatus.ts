// Single source of truth for status-pill labels in the chat thread.
//
// Each entry maps a backend tool name to a small spec describing what label
// to show during each lifecycle phase of an AG-UI tool call:
//
//   executing → args are complete, tool body is running
//   complete  → tool returned its result
//
// Adding a new backend tool: write the tool, then add one entry here. A single
// wildcard `useCopilotAction({name: "*"})` in `hooks/useToolStatus.tsx` renders
// the pill for ANY tool by looking up `TOOL_STATUS[name]` — no per-tool
// registration. Backend stays pure data — no status strings live in tool
// bodies; UI copy lives here.
//
// One entry gives the tool full control of both lifecycle phases. e.g. a
// future `locate_place` tool:
//   locate_place: {
//     executing: (a) => `Finding ${a?.query ?? "location"}`,  // during
//     complete:  ()  => "",                                   // after (silent)
//   }
// The run-level Thinking/streaming/idle phases are owned separately by
// `useAgentPhase` — see agent-compound-docs/decisions/frontend-status-lifecycle.md.

// A running label that rotates through verbs (e.g. "Checking" → "Reviewing"),
// with an optional fixed suffix and trailing string (e.g. a progress percent).
// Rendered by ToolPill via <RotatingWord>. Labels carry NO trailing "…" — the
// pill appends animated dots itself.
export interface RotatingExecuting {
  verbs: readonly string[];
  suffix?: string;
  trailing?: string;
}

export interface ToolUiSpec {
  // Static running label (no trailing "…" — animated dots are appended by the pill).
  executing: (args: any) => string;
  // Optional richer running label that rotates verbs + shows progress. Takes the
  // current SessionState so it can derive things like a percent from total_results.
  executingRotating?: (args: any, state: any) => RotatingExecuting;
  complete?: (args: any, result: any, state: any) => string;
}

// Backend default; the LLM may override via the page_size arg (see compute below).
const DEFAULT_PAGE_SIZE = 10;

export const TOOL_STATUS: Record<string, ToolUiSpec> = {
  search_apartments: {
    executing: (a: { districts?: string[] | null }) =>
      a?.districts?.length
        ? `Searching ${a.districts.join(", ")}`
        : "Searching apartments",
    complete: (_a, result) => firstLine(result) || "Search complete.",
  },

  get_result_page: {
    // Fallback if the rotating variant can't render for some reason.
    executing: () => "Looking through apartments",
    executingRotating: (
      a: { page?: number; page_size?: number },
      state: { total_results?: number } | null,
    ) => {
      const page = a?.page ?? 1;
      const pageSize = a?.page_size ?? DEFAULT_PAGE_SIZE;
      const total = state?.total_results ?? 0;
      const pct =
        total > 0
          ? Math.min(100, Math.round(((page * pageSize) / total) * 100))
          : null;
      return {
        verbs: PAGE_VERBS,
        suffix: " apartments",
        trailing: pct != null ? ` · ${pct}%` : undefined,
      };
    },
    // No completion pill for pagination — only the live "Checking apartments ·
    // NN%" matters; once the page loads, the cards/map carry the result. (""
    // → ToolPill renders nothing.) The backend result string ("Page N/M…") is
    // deliberately never echoed.
    complete: () => "",
  },

  open_listing: {
    executing: (a: { indices?: number[] }) =>
      `Looking up listing #${a?.indices?.[0] ?? "?"}`,
    complete: (a: { indices?: number[] }) =>
      `Opened listing #${a?.indices?.[0] ?? "?"}`,
  },

  locate_place: {
    executing: (a: { place_name?: string }) =>
      a?.place_name ? `Locating ${a.place_name}…` : "Locating place…",
    complete: (a: { place_name?: string }) =>
      a?.place_name ? `Found ${a.place_name}` : "Place located",
  },

  apply_travel_time: {
    executing: (a: { mode?: string }) => {
      const how = a?.mode === "car" ? "driving" : "transit";
      return `Computing ${how} times`;
    },
    // SHORT label (the place name isn't in the args — near_place_ref is an opaque
    // token — so read the human label off the lens the tool just set). Echoing
    // the full prose result here made the pill wrap into a misaligned block.
    complete: (_a, _r, state: { marker_lens?: { label?: string } } | null) => {
      const label = state?.marker_lens?.label;
      return label ? `Map coloured · ${label}` : "Travel times applied";
    },
  },

  clear_lens: {
    executing: () => "Removing lens",
    complete: () => "Lens removed",
  },
};

// Rotating verb sets. Module-level (stable refs) so <RotatingWord>'s timer
// doesn't restart on every render.
export const PAGE_VERBS = ["Checking", "Reviewing", "Browsing"] as const;
export const THINKING_VERBS = [
  "Thinking",
  "Reasoning",
  "Pondering",
  "Working",
] as const;

// Static fallback / aria label for the thinking phase.
export const THINKING_LABEL = "Thinking";

// First non-empty headline line of a tool's return value, used as the default
// `complete` label when the registry doesn't override. Skips leading "Note: …"
// soft-fallback lines (those carry caveats, not the headline) and defensively
// trims "--- Listing #3 ---" banner artifacts.
export function firstLine(value: unknown): string {
  if (typeof value !== "string") return "";
  for (const raw of value.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("Note:")) continue;
    if (line.startsWith("---") && line.endsWith("---")) {
      return line.replace(/---/g, "").trim();
    }
    return line;
  }
  return "";
}
