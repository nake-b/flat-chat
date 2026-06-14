// Single source of truth for status-pill labels in the chat thread.
//
// Each entry maps a backend tool name to a small spec describing what label
// to show during each lifecycle phase of an AG-UI tool call:
//
//   executing → args are complete, tool body is running
//   complete  → tool returned its result
//
// Adding a new backend tool: write the tool, then add one entry here AND
// one matching `useToolPill("<name>")` call in `ChatPane.tsx`. Backend stays
// pure data — no status strings live in tool bodies; UI copy lives here.

export interface ToolUiSpec {
  executing: (args: any) => string;
  complete?: (args: any, result: any) => string;
}

export const TOOL_STATUS: Record<string, ToolUiSpec> = {
  search_apartments: {
    executing: (a: { districts?: string[] | null }) =>
      a?.districts?.length
        ? `Searching ${a.districts.join(", ")}…`
        : "Searching apartments…",
    complete: (_a, result) => firstLine(result) || "Search complete.",
  },

  get_result_page: {
    executing: (a: { page?: number }) => `Loading page ${a?.page ?? 1}…`,
    complete: (_a, result) => firstLine(result) || "Page loaded.",
  },

  open_listing: {
    executing: (a: { indices?: number[] }) =>
      `Looking up listing #${a?.indices?.[0] ?? "?"}…`,
    complete: (a: { indices?: number[] }) =>
      `Opened listing #${a?.indices?.[0] ?? "?"}`,
  },
};

export const THINKING_LABEL = "Thinking…";

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
