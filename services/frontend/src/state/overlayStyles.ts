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
