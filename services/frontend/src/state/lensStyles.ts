// Marker-lens appearance registry — the FRONTEND owns how the active map
// visualization lens looks.
//
// The backend sets only SEMANTICS on `SessionState.marker_lens` (key +
// human label) and ships the per-marker scalar in `result_markers.values`.
// The colour ramp, numeric domain and number format live here, keyed off
// `key` — same division of labour as `overlayStyles.ts` (overlays) and
// `toolStatus.ts` (status pills). Presentation stays on the frontend, never in
// agent/state payloads.
//
// Only ONE lens is ever active (you can't colour pins by two scalars at
// once). The default `price_warm` has NO ramp here → the map renders the plain
// pin (today's grey/hover look, no heatmap). A ramped lens (`commute_min`)
// → pins interpolate over `lens_value`. Adding a future lens (e.g. a
// noise heatmap) is one entry below + the backend populating that scalar.
//
// Consumed by MapPane.tsx (pin paint) and LensLegend.tsx (the legend).

import type { ExpressionSpecification } from "maplibre-gl";
import type { MarkerLens } from "./SessionState";

export interface LensStyle {
  // The ramp's NATIVE [min, max] — the window the ramp stops are written
  // against. Used as the fallback domain AND as the basis for remapping when an
  // adaptive per-result domain is supplied (see `lensDomain`): the stops are
  // kept native so the same ramp can be stretched onto any window without
  // rewriting it. Adaptive scaling is computed on the frontend from the markers
  // it already has — no backend/state field.
  domain: [number, number];
  // [value, colour] stops within `domain`, ascending.
  ramp: Array<[number, string]>;
  // Short title for the legend (the per-anchor specifics come from
  // `marker_lens.label`, e.g. "min to TU Berlin").
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
const COMMUTE: LensStyle = {
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

// Straight-line distance: a sequential BLUE ramp, 0–5 km, near = deep blue →
// far = pale blue. A different hue from the travel-time red so the two lenses
// are instantly distinguishable at a glance; same vibrant-near / faded-far
// reading. `lens_value` arrives in METRES (the backend's ST_Distance unit); the
// formatter renders km. Blue (not green→red) keeps "far" from reading as "bad".
const DISTANCE: LensStyle = {
  domain: [0, 5000],
  ramp: [
    [0, "#1D4ED8"],
    [1250, "#3B82F6"],
    [2500, "#60A5FA"],
    [3750, "#93C5FD"],
    [5000, "#DBEAFE"],
  ],
  legendTitle: "Distance",
  format: (v) => `${(v / 1000).toFixed(1)} km`,
};

// Keyed by `MarkerLens.key`. `price_warm` is intentionally ABSENT — the
// default lens has no heatmap (plain pins). Add ramped lenses here.
export const LENS_STYLES: Record<string, LensStyle> = {
  commute_min: COMMUTE,
  distance_m: DISTANCE,
};

// Colour for a marker whose `lens_value` is null (no price / unreachable in
// the active travel lens).
export const NO_DATA_COLOR = "#9AA0A6";

export function lensStyle(
  lens: MarkerLens | null | undefined,
): LensStyle | undefined {
  return lens ? LENS_STYLES[lens.key] : undefined;
}

// Adaptive domain for the active lens, computed from the actual marker values.
// The static ramp spans a wide native window (commute 0–60 min) but a real
// result set clusters in a narrow band (5–35 min), which washes out at the pale
// end. Returning [floor(min), ceil(max)] stretches the ramp over the real spread
// so contrast tracks the data. Returns `undefined` for a non-heatmap lens, and
// falls back to the native `style.domain` when there are no values yet.
export function lensDomain(
  values: ReadonlyArray<number | null | undefined>,
  lens: MarkerLens | null | undefined,
): [number, number] | undefined {
  const style = lensStyle(lens);
  if (!style) return undefined;
  const nums = values.filter((v): v is number => typeof v === "number");
  if (!nums.length) return style.domain;
  const lo = Math.floor(Math.min(...nums));
  let hi = Math.ceil(Math.max(...nums));
  if (hi <= lo) hi = lo + 1; // guard a degenerate single-value set
  return [lo, hi];
}

export interface LensLegendSpec {
  title: string; // e.g. "min to TU Berlin" (falls back to the style title)
  gradient: string; // a CSS linear-gradient(...) for the ramp swatch
  minLabel: string;
  maxLabel: string;
}

// Legend spec for the active lens, or null when there's no heatmap (the
// default `price_warm` lens renders plain pins → nothing to explain).
export function lensLegend(
  lens: MarkerLens | null | undefined,
  domain?: [number, number],
): LensLegendSpec | null {
  const style = lensStyle(lens);
  if (!style) return null;
  // The swatch always shows the full ramp (relative % over the ramp's native
  // window); only the min/max LABELS reflect the active (adaptive) domain.
  const [nLo, nHi] = style.domain;
  const span = nHi - nLo || 1;
  const stops = style.ramp
    .map(([v, c]) => `${c} ${Math.round(((v - nLo) / span) * 100)}%`)
    .join(", ");
  const [lo, hi] = domain ?? style.domain;
  return {
    title: lens?.label || style.legendTitle,
    gradient: `linear-gradient(to right, ${stops})`,
    minLabel: style.format(lo),
    maxLabel: style.format(hi),
  };
}

// Whether the active lens paints a heatmap (vs. the plain default pin).
export function isHeatmapLens(
  lens: MarkerLens | null | undefined,
): boolean {
  return lensStyle(lens) !== undefined;
}

// Shared ramp→colour builder: interpolate the active lens's ramp over an
// arbitrary numeric VALUE expression, falling back to NO_DATA_COLOR when the
// `noData` predicate holds. Returns `undefined` for the default/unknown lens
// (no heatmap). Both the pin paint (value = each marker's `lens_value`) and
// the cluster paint (value = the cluster's MEAN of `lens_value`) compose on
// this one builder so a single ramp drives both — change the ramp once, both
// follow. Caller layers hover/selection highlight on top.
export function rampColorExpression(
  lens: MarkerLens | null | undefined,
  value: ExpressionSpecification,
  noData: ExpressionSpecification,
  domain?: [number, number],
): ExpressionSpecification | undefined {
  const style = lensStyle(lens);
  if (!style) return undefined;
  // Remap the native ramp stops onto the active domain (adaptive contrast):
  // each native stop value keeps its RELATIVE position and is restretched over
  // [lo,hi]. With no domain override this is the identity (native window).
  const [nLo, nHi] = style.domain;
  const nSpan = nHi - nLo || 1;
  const [lo, hi] = domain ?? style.domain;
  const stops = style.ramp.flatMap(([v, c]) => [
    lo + ((v - nLo) / nSpan) * (hi - lo),
    c,
  ]);
  return [
    "case",
    noData,
    NO_DATA_COLOR,
    ["interpolate", ["linear"], value, ...stops],
  ] as ExpressionSpecification;
}

// MapLibre expression for the BASE pin colour under the active lens.
// Returns `plain` (the default grey) for `price_warm`/unknown keys; otherwise
// an interpolation over each marker's `lens_value` with null → NO_DATA_COLOR.
// `domain` (optional) stretches the ramp to the result's actual spread.
export function lensColorExpression(
  lens: MarkerLens | null | undefined,
  plain: string,
  domain?: [number, number],
): string | ExpressionSpecification {
  return (
    rampColorExpression(
      lens,
      ["to-number", ["get", "lens_value"]] as ExpressionSpecification,
      ["==", ["get", "lens_value"], null] as ExpressionSpecification,
      domain,
    ) ?? plain
  );
}

// Plain-JS colour (a hex string) for ONE value under the active lens ramp — the
// same interpolation `rampColorExpression` does for the map, but for DOM use
// (a card's lens badge / stat), so the card matches its map pin. Returns null
// for the default/unknown lens (no heatmap) or a null/non-finite value, so the
// caller can fall back to its own styling.
export function lensColorForValue(
  lens: MarkerLens | null | undefined,
  value: number | null | undefined,
  domain?: [number, number],
): string | null {
  const style = lensStyle(lens);
  if (!style || typeof value !== "number" || !Number.isFinite(value)) return null;
  const [nLo, nHi] = style.domain;
  const nSpan = nHi - nLo || 1;
  const [lo, hi] = domain ?? style.domain;
  const stops = style.ramp.map(
    ([v, c]) => [lo + ((v - nLo) / nSpan) * (hi - lo), c] as [number, string],
  );
  if (value <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (value >= last[0]) return last[1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [v0, c0] = stops[i];
    const [v1, c1] = stops[i + 1];
    if (value >= v0 && value <= v1) {
      return lerpHex(c0, c1, (value - v0) / (v1 - v0 || 1));
    }
  }
  return last[1];
}

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function lerpHex(a: string, b: string, t: number): string {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const ch = (i: number) => Math.round(pa[i] + (pb[i] - pa[i]) * t);
  return `#${[ch(0), ch(1), ch(2)]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("")}`;
}
