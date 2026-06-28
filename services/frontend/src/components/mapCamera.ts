import type { MarkerPoint } from "../state/SessionState";

// Pure helpers for the map's "new result set" camera reframe. Kept out of
// MapPane (and free of any maplibre types) so the gating rule — the bit with
// real UX judgement in it — is unit-testable without a map instance. The
// component adapts maplibre's `getBounds()` into the plain `LngLatRect` below.

// Below this zoom the user is in "overview" mode (looking at most of Berlin),
// so a new search should reframe to the results. At/above it they've focused
// on a kiez and we respect that — unless the results aren't on screen at all.
export const OVERVIEW_MAX_ZOOM = 11.5;

// If fewer than this share of the new markers fall inside the current
// viewport, reframe even when zoomed in — otherwise the user stares at a
// viewport with (almost) none of their results and thinks the search broke.
export const IN_VIEW_MIN = 0.1;

// Cap the reframe so a single / tight result set doesn't slam to max zoom.
export const REFRAME_MAX_ZOOM = 14;
// Eased camera glide for the reframe (ms). Runs alongside the marker fade.
export const REFRAME_MS = 700;

export interface LngLatRect {
  west: number;
  south: number;
  east: number;
  north: number;
}

// [[west, south], [east, north]] — the LngLatBoundsLike shape maplibre's
// fitBounds accepts. Null for an empty set (nothing to frame).
export type BBox = [[number, number], [number, number]];

export function markersBBox(markers: MarkerPoint[]): BBox | null {
  if (markers.length === 0) return null;
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const m of markers) {
    if (m.lng < west) west = m.lng;
    if (m.lng > east) east = m.lng;
    if (m.lat < south) south = m.lat;
    if (m.lat > north) north = m.lat;
  }
  return [
    [west, south],
    [east, north],
  ];
}

// Share (0..1) of markers whose coordinates fall inside `rect`. 0 for an empty
// set. Inclusive on the edges — a marker exactly on the boundary counts as in.
export function fractionInside(
  markers: MarkerPoint[],
  rect: LngLatRect,
): number {
  if (markers.length === 0) return 0;
  let inside = 0;
  for (const m of markers) {
    if (
      m.lng >= rect.west &&
      m.lng <= rect.east &&
      m.lat >= rect.south &&
      m.lat <= rect.north
    ) {
      inside++;
    }
  }
  return inside / markers.length;
}

export interface ReframeDecision {
  zoom: number;
  fractionInView: number;
  hasActiveSelection: boolean;
  markerCount: number;
}

// Whether a new result set should reframe the camera. The rule:
//   - never when there's nothing to frame, or a listing is selected (the
//     selection pan owns the camera — don't yank away on a refinement);
//   - otherwise reframe when zoomed out (overview) OR the results aren't on
//     screen (would-be empty viewport);
//   - otherwise stay put (zoomed in and refining within the current view).
export function shouldReframe({
  zoom,
  fractionInView,
  hasActiveSelection,
  markerCount,
}: ReframeDecision): boolean {
  if (markerCount === 0 || hasActiveSelection) return false;
  return zoom < OVERVIEW_MAX_ZOOM || fractionInView < IN_VIEW_MIN;
}
