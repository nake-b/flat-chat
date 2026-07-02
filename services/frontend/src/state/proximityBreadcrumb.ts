// "9.4 km to FU Berlin" / "19 min to FU Berlin by car" proximity-finish copy.
//
// The single-listing tools `distance_to` / `travel_time_to` return a prose
// string whose success form embeds the resolved place name (`anchor.label`) and
// the measured value. `toolStatus.ts` calls this to turn that into the finish
// label shown on the tool-call pill — both live and on reload, since the result
// string is part of the persisted transcript (see `frontend-status-lifecycle.md`).
// Pure so it unit-tests without React.
//
// Contract with `chat/tools/proximity.py` SUCCESS formats (pinned there by
// `tests/unit/test_proximity_tools.py`; a reword fails those tests rather than
// silently breaking this parser):
//   distance: "{origin} is about {km} km from {label} (straight-line distance)."
//   travel:   "{origin} is about {min} min from {label} by car|by public transport.{stale?}"
// Every other return (origin-miss guidance, coord-missing, unreachable, routing
// failure) is NOT a measurement → "" (the pill renders nothing, matching search).

// Non-greedy label capture anchored on the known suffix, so a label with inner
// spaces ("FU Berlin", "the Spree") is captured whole up to the fixed tail.
const DISTANCE_RE = /\bis about ([\d.]+) km from (.+?) \(straight-line distance\)\./;
const TRAVEL_RE = /\bis about (\d+) min from (.+?) (by car|by public transport)\./;

/**
 * Finish label for a proximity tool result, or "" when the string isn't a
 * successful measurement (so the pill stays silent, as it does for a failed
 * search).
 */
export function proximityBreadcrumb(content: unknown): string {
  if (typeof content !== "string") return "";

  const d = content.match(DISTANCE_RE);
  if (d) return `${d[1]} km to ${d[2]}`;

  const t = content.match(TRAVEL_RE);
  if (t) {
    const how = t[3] === "by car" ? "by car" : "by transit";
    return `${t[1]} min to ${t[2]} ${how}`;
  }

  return "";
}
