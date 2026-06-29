// Marker-channel appearance registry — the FRONTEND owns how the active map
// visualization channel looks.
//
// The backend sets only SEMANTICS on `SessionState.marker_channel` (key +
// human label) and ships the per-marker scalar in `result_markers.values`.
// The colour ramp, numeric domain and number format live here, keyed off
// `key` — same division of labour as `overlayStyles.ts` (overlays) and
// `toolStatus.ts` (status pills). Presentation stays on the frontend, never in
// agent/state payloads.
//
// Only ONE channel is ever active (you can't colour pins by two scalars at
// once). The default `price_warm` has NO ramp here → the map renders the plain
// pin (today's grey/hover look, no heatmap). A ramped channel (`commute_min`)
// → pins interpolate over `channel_value`. Adding a future channel (e.g. a
// noise heatmap) is one entry below + the backend populating that scalar.
//
// Consumed by MapPane.tsx (pin paint) and ChannelLegend.tsx (the legend).

import type { ExpressionSpecification } from "maplibre-gl";
import type { MarkerChannel } from "./SessionState";

export interface ChannelStyle {
  // Fixed [min, max] for the ramp — deliberately NOT computed from the current
  // values, so "red = far" means the same thing every turn (comparable across
  // searches). If a channel ever needs adaptive scaling, the backend can ship
  // an explicit domain on `marker_channel`.
  domain: [number, number];
  // [value, colour] stops within `domain`, ascending.
  ramp: Array<[number, string]>;
  // Short title for the legend (the per-anchor specifics come from
  // `marker_channel.label`, e.g. "min to TU Berlin").
  legendTitle: string;
  // Format one scalar for the legend / tooltip.
  format: (v: number) => string;
}

// Travel time: green (near) → red (far), 0–60 min.
const COMMUTE: ChannelStyle = {
  domain: [0, 60],
  ramp: [
    [0, "#1A9850"],
    [15, "#A6D96A"],
    [30, "#FEE08B"],
    [45, "#F46D43"],
    [60, "#D73027"],
  ],
  legendTitle: "Travel time",
  format: (v) => `${Math.round(v)} min`,
};

// Keyed by `MarkerChannel.key`. `price_warm` is intentionally ABSENT — the
// default channel has no heatmap (plain pins). Add ramped channels here.
export const CHANNEL_STYLES: Record<string, ChannelStyle> = {
  commute_min: COMMUTE,
};

// Colour for a marker whose `channel_value` is null (no price / unreachable in
// the active travel lens).
export const NO_DATA_COLOR = "#9AA0A6";

export function channelStyle(
  channel: MarkerChannel | null | undefined,
): ChannelStyle | undefined {
  return channel ? CHANNEL_STYLES[channel.key] : undefined;
}

export interface ChannelLegendSpec {
  title: string; // e.g. "min to TU Berlin" (falls back to the style title)
  gradient: string; // a CSS linear-gradient(...) for the ramp swatch
  minLabel: string;
  maxLabel: string;
}

// Legend spec for the active channel, or null when there's no heatmap (the
// default `price_warm` channel renders plain pins → nothing to explain).
export function channelLegend(
  channel: MarkerChannel | null | undefined,
): ChannelLegendSpec | null {
  const style = channelStyle(channel);
  if (!style) return null;
  const [lo, hi] = style.domain;
  const span = hi - lo || 1;
  const stops = style.ramp
    .map(([v, c]) => `${c} ${Math.round(((v - lo) / span) * 100)}%`)
    .join(", ");
  return {
    title: channel?.label || style.legendTitle,
    gradient: `linear-gradient(to right, ${stops})`,
    minLabel: style.format(lo),
    maxLabel: style.format(hi),
  };
}

// Whether the active channel paints a heatmap (vs. the plain default pin).
export function isHeatmapChannel(
  channel: MarkerChannel | null | undefined,
): boolean {
  return channelStyle(channel) !== undefined;
}

// MapLibre expression for the BASE pin colour under the active channel.
// Returns `plain` (the default grey) for `price_warm`/unknown keys; otherwise
// an interpolation over `channel_value` with null → NO_DATA_COLOR. The caller
// composes hover highlight on top.
export function channelColorExpression(
  channel: MarkerChannel | null | undefined,
  plain: string,
): string | ExpressionSpecification {
  const style = channelStyle(channel);
  if (!style) return plain;
  const stops = style.ramp.flatMap(([v, c]) => [v, c]);
  return [
    "case",
    ["==", ["get", "channel_value"], null],
    NO_DATA_COLOR,
    [
      "interpolate",
      ["linear"],
      ["to-number", ["get", "channel_value"]],
      ...stops,
    ],
  ] as ExpressionSpecification;
}
