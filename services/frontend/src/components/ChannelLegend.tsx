// Legend for the active map visualization channel (the commute heatmap). A DOM
// layer over the map, sibling of <MapLibreMap> in MapPane's relative wrapper.
//
// Renders nothing on the default `price_warm` channel (plain pins, nothing to
// explain) — only when a ramped channel (e.g. commute) is active. Appearance
// (ramp/domain/format) comes from `state/channelStyles.ts`; the per-anchor
// title comes from `marker_channel.label`.

import { useSessionState } from "../hooks/useSessionState";
import { channelLegend } from "../state/channelStyles";

export function ChannelLegend() {
  const { state } = useSessionState();
  const legend = channelLegend(state?.marker_channel);
  if (!legend) return null;

  return (
    <div className="absolute bottom-3 left-3 z-10 rounded-lg bg-white/95 px-3 py-2 text-xs text-neutral-800 shadow-sm ring-1 ring-black/10">
      <div className="mb-1 font-medium">{legend.title}</div>
      <div
        className="h-2 w-40 rounded-full"
        style={{ background: legend.gradient }}
        aria-hidden
      />
      <div className="mt-0.5 flex justify-between font-mono text-[10px] text-neutral-500">
        <span>{legend.minLabel}</span>
        <span>{legend.maxLabel}</span>
      </div>
    </div>
  );
}
