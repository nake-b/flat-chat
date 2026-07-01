// Legend for the active map visualization lens (the commute heatmap). A DOM
// layer over the map, sibling of <MapLibreMap> in MapPane's relative wrapper.
//
// Renders nothing on the default `price_warm` lens (plain pins, nothing to
// explain) — only when a ramped lens (e.g. commute) is active. Appearance
// (ramp/format) comes from `state/lensStyles.ts`; the per-anchor title comes
// from `marker_lens.label`. The numeric min/max reflect the ADAPTIVE domain
// computed from the actual markers (same as the pins/clusters).
//
// The × dismisses the lens — the lens analogue of OverlayLegend's per-overlay
// dismiss. It clears the lens locally; the backend honours it as authoritative
// on the next turn (see useSessionState.dismissLens + merge_incoming_state).

import { useMemo } from "react";

import { useSessionState } from "../hooks/useSessionState";
import { decodeMarkers } from "../state/SessionState";
import { lensDomain, lensLegend } from "../state/lensStyles";

export function LensLegend() {
  const { state, dismissLens } = useSessionState();
  const lens = state?.marker_lens;

  const domain = useMemo(
    () => lensDomain(decodeMarkers(state?.result_markers).map((m) => m.lens_value), lens),
    [state?.result_markers, lens],
  );

  const legend = lensLegend(lens, domain);
  if (!legend) return null;

  // When the transit feed has lapsed the backend clamps the departure and flags
  // it — surface the schedule's age so the minutes aren't read as live.
  const filter = state?.travel_time_filter;
  const staleAsOf =
    filter?.schedule_stale && filter?.schedule_as_of ? filter.schedule_as_of : null;

  return (
    <div className="absolute bottom-3 left-3 z-10 rounded-lg bg-white/95 px-3 py-2 text-xs text-neutral-800 shadow-sm ring-1 ring-black/10">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-medium">{legend.title}</span>
        <button
          type="button"
          onClick={() => dismissLens()}
          aria-label="Remove lens"
          title="Remove lens"
          className="ml-auto flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-neutral-400 transition-colors hover:bg-neutral-200 hover:text-neutral-700"
        >
          ×
        </button>
      </div>
      <div
        className="h-2 w-40 rounded-full"
        style={{ background: legend.gradient }}
        aria-hidden
      />
      <div className="mt-0.5 flex justify-between font-mono text-[10px] text-neutral-500">
        <span>{legend.minLabel}</span>
        <span>{legend.maxLabel}</span>
      </div>
      {staleAsOf && (
        <div className="mt-1 text-[10px] leading-tight text-neutral-400">
          schedule as of {staleAsOf}
        </div>
      )}
    </div>
  );
}
