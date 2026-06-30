// "Found N apartments" search-finish copy — parse + format.
//
// The `search_apartments` tool returns a summary string whose first line is
// `Found N listings, …` (or `No apartments found …`). `toolStatus.ts` calls
// these to turn that into the finish label shown on the tool-call pill, both
// live and on reload (the result string is part of the persisted transcript —
// see `frontend-status-lifecycle.md`). Pure so they unit-test without React.

/**
 * Parse the backend search summary's first line into a result count.
 *
 * Contract with `chat/llm_context.py:LlmResultSetView.summary`:
 *   - hits  → first line is `Found {n} listings, {order}.`
 *   - empty → first line is `No apartments found matching those criteria. …`
 * Pinned on the backend by `tests/unit/test_llm_context.py`; a reword there
 * fails those tests rather than silently breaking this parser.
 *
 * Returns the count, or null if the string isn't a recognizable search summary.
 */
export function parseSearchCount(content: unknown): number | null {
  if (typeof content !== "string") return null;
  const first = content.split("\n").find((l) => l.trim().length > 0)?.trim() ?? "";
  const m = first.match(/^Found\s+([\d,]+)\s+listings?/i);
  if (m) return Number.parseInt(m[1].replace(/,/g, ""), 10);
  if (/^no apartments found/i.test(first)) return 0;
  return null;
}

/** Breadcrumb copy for a final result count. */
export function formatSearchBreadcrumb(count: number): string {
  if (count <= 0) return "No apartments found — try broadening your search";
  return `Found ${count} apartment${count === 1 ? "" : "s"}`;
}
