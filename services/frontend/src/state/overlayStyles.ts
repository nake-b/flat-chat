// Overlay appearance registry — the FRONTEND owns how map overlays look.
//
// The backend sets only semantics on a MapOverlay (kind / label / geojson);
// colors, opacity, and line-vs-fill live here, keyed off `kind` + the geojson
// geometry type. This mirrors how the apartment-marker paint lives in
// MapPane.tsx and how status-pill labels live in toolStatus.ts — presentation
// stays on the frontend, not in agent/state payloads.
//
// Consumed by `OverlayLayer` in MapPane.tsx.

import type { MapOverlay } from "./SessionState";

export type OverlayShape = "line" | "fill" | "point";

// What MapLibre layer to draw for a given geometry. Polygons get a translucent
// fill (+ outline); lines get a stroke; points get a dot.
export function overlayShape(geometry: GeoJSON.Geometry): OverlayShape {
  switch (geometry.type) {
    case "Polygon":
    case "MultiPolygon":
      return "fill";
    case "LineString":
    case "MultiLineString":
      return "line";
    default:
      return "point";
  }
}

// Berlin U-/S-Bahn line colours (the familiar network-map palette), keyed by
// the uppercased line label. Anything not listed (regional, tram, bus, an
// unknown line) falls back to a neutral transit grey.
const TRANSIT_LINE_COLORS: Record<string, string> = {
  U1: "#7DAD4C",
  U2: "#DA421E",
  U3: "#16683D",
  U4: "#F0D722",
  U5: "#7E5330",
  U6: "#8C6DAB",
  U7: "#528DBA",
  U8: "#224F86",
  U9: "#F3791D",
  S1: "#DE4DA4",
  S2: "#007734",
  S3: "#0A4C99",
  S5: "#FB5D08",
  S7: "#7B4F9D",
  S8: "#6FA228",
  S9: "#992746",
  S25: "#007734",
  S41: "#A23B1E",
  S42: "#C06228",
  S45: "#C5994C",
  S46: "#C5994C",
  S47: "#C5994C",
};

const TRANSIT_DEFAULT = "#555B66"; // regional/tram/bus/unknown line
const WATER_BLUE = "#3A8DDE"; // a place that's a river/canal line
const PLACE_GREEN = "#3F9D4F"; // a place polygon (park, named green space)
const RING_RED = "#B00030"; // the S-Bahn-ring / Umweltzone zone
const BEZIRK_PURPLE = "#7E5AA2"; // a Bezirk / Ortsteil boundary

// Resolve the colour for an overlay given how it will be drawn. `shape`
// disambiguates `place` (a river line → water-blue, a park polygon → green).
export function overlayColor(overlay: MapOverlay, shape: OverlayShape): string {
  switch (overlay.kind) {
    case "transit_line":
      return TRANSIT_LINE_COLORS[overlay.label.toUpperCase()] ?? TRANSIT_DEFAULT;
    case "ring":
      return RING_RED;
    case "bezirk":
      return BEZIRK_PURPLE;
    case "parks":
      return PLACE_GREEN;
    case "place":
      return shape === "line" ? WATER_BLUE : PLACE_GREEN;
    default:
      return TRANSIT_DEFAULT;
  }
}

// Paint constants — translucent fills so markers stay readable on top.
export const OVERLAY_FILL_OPACITY = 0.18;
export const OVERLAY_OUTLINE_WIDTH = 2;
export const OVERLAY_LINE_WIDTH = 4;
export const OVERLAY_LINE_OPACITY = 0.9;
export const OVERLAY_POINT_RADIUS = 7;

// ── Hybrid treatment ──────────────────────────────────────────────────────
// Decision (mocked + chosen): CRISP lines + badges; a soft GLOW HALO on area
// overlays (parks/lakes/buildings/ring/bezirk) where "this whole region is what
// you asked about" is the message — and where the breathing animation lives.
// The values below are the RESTING look (also the prefers-reduced-motion
// fallback); MapPane's rAF loop modulates them when motion is allowed.

// Area overlays (anything drawn as a fill) get a blurred boundary halo drawn
// beneath the fill — that halo layer is what the breathing animation pulses.
//
// Lines that read as a "flow" carry a thin animated dash shimmer over the crisp
// base line: transit lines (a route) and a place drawn as a line (a river/canal).
export function overlayLineFlows(overlay: MapOverlay, shape: OverlayShape): boolean {
  return shape === "line" && (overlay.kind === "transit_line" || overlay.kind === "place");
}

// Halo (area breathing target). Wide + blurred so it reads as an aura, not a stroke.
export const OVERLAY_HALO_WIDTH = 13;
export const OVERLAY_HALO_BLUR = 7;
export const OVERLAY_HALO_OPACITY_MIN = 0.12; // breath trough
export const OVERLAY_HALO_OPACITY_MAX = 0.36; // breath crest (also the static look)


// The fill ALSO breathes (not just the halo) so a small building footprint reads
// as actively "selected", not just outlined — the halo alone is invisible on a
// building-sized polygon. Gentle band around OVERLAY_FILL_OPACITY so big parks
// don't throb. OVERLAY_FILL_OPACITY remains the static / reduced-motion value.
export const OVERLAY_FILL_OPACITY_MIN = 0.1;
export const OVERLAY_FILL_OPACITY_MAX = 0.26;

// Flow shimmer — a thin light dash that marches along a line over the crisp base.
export const OVERLAY_FLOW_WIDTH = 2;
export const OVERLAY_FLOW_OPACITY = 0.55;
export const OVERLAY_FLOW_COLOR = "#ffffff";

// Transit station dots + their pulsing aura (the "active station" signal).
// Kept small — a line has many stops, so big dots crowd the map and fight pins.
export const STATION_RADIUS = 3;
export const STATION_STROKE_WIDTH = 1.5;
export const STATION_FILL = "#ffffff";
export const STATION_AURA_RADIUS_MIN = 4;
export const STATION_AURA_RADIUS_MAX = 8;
export const STATION_AURA_OPACITY = 0.28; // at trough; fades to 0 as it expands

// Line badge ("U8") — rendered as a runtime canvas image (see MapPane).
export const BADGE_TEXT_COLOR = "#ffffff";

// Animation cadence. Entrance plays once when an overlay appears; breathing /
// pulsing are slow and continuous. All disabled under prefers-reduced-motion.
export const OVERLAY_ENTRANCE_MS = 450;
export const OVERLAY_BREATH_PERIOD_MS = 2000; // faster, more "alive" pulse
export const STATION_PULSE_PERIOD_MS = 2600;
export const OVERLAY_FLOW_STEP_MS = 70; // ms per dash-sequence frame

// Dash patterns cycled to fake a marching/flowing line (MapLibre has no
// line-dash-offset). Each is a [dash, gap, …] in line-width units; stepping the
// index over time shifts the dashes along the line.
export const FLOW_DASH_SEQUENCE: number[][] = [
  [0, 4, 3],
  [0.5, 4, 2.5],
  [1, 4, 2],
  [1.5, 4, 1.5],
  [2, 4, 1],
  [2.5, 4, 0.5],
  [3, 4, 0],
  [0, 0.5, 3, 3.5],
  [0, 1, 3, 3],
  [0, 1.5, 3, 2.5],
  [0, 2, 3, 2],
  [0, 2.5, 3, 1.5],
  [0, 3, 3, 1],
  [0, 3.5, 3, 0.5],
];
