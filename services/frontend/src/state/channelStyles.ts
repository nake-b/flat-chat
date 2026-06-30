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

// Travel time: a Berlin-red SEQUENTIAL ramp, 0–60 min, near = VIBRANT brand red
// → far = WEAK/pale pink. Deliberately single-hue rather than green→red — it
// sits in a UI whose whole marker/cluster language is already red, so a green
// "good" end would clash with the brand and read as a different control.
// Vibrant-near / faded-far reads intuitively: the listings you can reach
// quickly are saturated and grab attention; far ones recede toward the
// background. Stays legible on the light basemap and on-palette throughout.
const COMMUTE: ChannelStyle = {
  domain: [0, 60],
  ramp: [
    [0, "#E4003C"],
    [15, "#EE4D6E"],
    [30, "#F47A95"],
    [45, "#F7A8BC"],
    [60, "#FCE7EC"],
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

// Shared ramp→colour builder: interpolate the active channel's ramp over an
// arbitrary numeric VALUE expression, falling back to NO_DATA_COLOR when the
// `noData` predicate holds. Returns `undefined` for the default/unknown channel
// (no heatmap). Both the pin paint (value = each marker's `channel_value`) and
// the cluster paint (value = the cluster's MEAN of `channel_value`) compose on
// this one builder so a single ramp drives both — change the ramp once, both
// follow. Caller layers hover/selection highlight on top.
export function rampColorExpression(
  channel: MarkerChannel | null | undefined,
  value: ExpressionSpecification,
  noData: ExpressionSpecification,
): ExpressionSpecification | undefined {
  const style = channelStyle(channel);
  if (!style) return undefined;
  const stops = style.ramp.flatMap(([v, c]) => [v, c]);
  return [
    "case",
    noData,
    NO_DATA_COLOR,
    ["interpolate", ["linear"], value, ...stops],
  ] as ExpressionSpecification;
}

// MapLibre expression for the BASE pin colour under the active channel.
// Returns `plain` (the default grey) for `price_warm`/unknown keys; otherwise
// an interpolation over each marker's `channel_value` with null → NO_DATA_COLOR.
export function channelColorExpression(
  channel: MarkerChannel | null | undefined,
  plain: string,
): string | ExpressionSpecification {
  return (
    rampColorExpression(
      channel,
      ["to-number", ["get", "channel_value"]] as ExpressionSpecification,
      ["==", ["get", "channel_value"], null] as ExpressionSpecification,
    ) ?? plain
  );
}
