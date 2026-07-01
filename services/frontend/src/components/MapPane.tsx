import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Map as MapLibreMap,
  Source,
  Layer,
  AttributionControl,
} from "@vis.gl/react-maplibre";
import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  GeoJSONSource,
  Map as MaplibreGl,
  MapLayerMouseEvent,
  SymbolLayerSpecification,
} from "maplibre-gl";
import type { Feature, FeatureCollection, Point } from "geojson";

import { useSessionState } from "../hooks/useSessionState";
import { useActiveIdMirror, useHover } from "../hooks/useHover";
import { decodeMarkers } from "../state/SessionState";
import type { MarkerLens } from "../state/SessionState";
import {
  lensColorExpression,
  lensDomain,
  lensStyle,
  rampColorExpression,
} from "../state/lensStyles";
import {
  BADGE_TEXT_COLOR,
  FLOW_DASH_SEQUENCE,
  OVERLAY_BREATH_PERIOD_MS,
  OVERLAY_ENTRANCE_MS,
  OVERLAY_FILL_OPACITY,
  OVERLAY_FILL_OPACITY_MAX,
  OVERLAY_FILL_OPACITY_MIN,
  OVERLAY_FLOW_COLOR,
  OVERLAY_FLOW_OPACITY,
  OVERLAY_FLOW_STEP_MS,
  OVERLAY_FLOW_WIDTH,
  OVERLAY_HALO_BLUR,
  OVERLAY_HALO_OPACITY_MAX,
  OVERLAY_HALO_OPACITY_MIN,
  OVERLAY_HALO_WIDTH,
  OVERLAY_LINE_OPACITY,
  OVERLAY_LINE_WIDTH,
  OVERLAY_OUTLINE_WIDTH,
  OVERLAY_POINT_RADIUS,
  STATION_AURA_OPACITY,
  STATION_AURA_RADIUS_MAX,
  STATION_AURA_RADIUS_MIN,
  STATION_FILL,
  STATION_PULSE_PERIOD_MS,
  STATION_RADIUS,
  STATION_STROKE_WIDTH,
  overlayColor,
  overlayLineFlows,
  overlayShape,
} from "../state/overlayStyles";
import { OverlayLegend } from "./OverlayLegend";
import { LensLegend } from "./LensLegend";
import {
  REFRAME_MAX_ZOOM,
  REFRAME_MS,
  fractionInside,
  markersBBox,
  shouldReframe,
} from "./mapCamera";

// Initial view: zoomed out far enough to see the whole Berlin outline.
// Berlin admin border roughly: lat 52.34 → 52.68, lng 13.09 → 13.76.
// The city spans ~38 km × 45 km — at zoom 9.4 with a typical map height
// the full silhouette fits with a hair of Brandenburg around it.
const BERLIN_CENTER = { latitude: 52.52, longitude: 13.405, zoom: 9.4 };

// MaxBounds clamps panning AND zoom-out — at low zoom levels the viewport
// must still fit inside this rectangle. We expand ~10–15% past the Berlin
// admin outline so the user can zoom out a notch further than the city
// silhouette and still see it framed cleanly. `minZoom` is a fallback cap.
const BERLIN_BOUNDS: [[number, number], [number, number]] = [
  [12.7, 52.18], // SW
  [14.1, 52.85], // NE
];

// Plain demo style for MVP. Swapped for our self-hosted Protomaps Berlin
// extract once the .pmtiles file is in place behind nginx (`/tiles/berlin.pmtiles`).
const MAP_STYLE_URL =
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

// Berlin-red palette for the markers. Cluster size steps from tint → deep
// as point_count grows, which doubles as a casual density legend at zoom-out.
const RED = "#E4003C";
const RED_DEEP = "#B00030";
const RED_TINT = "#F47A95";
const GREY = "#5A5A5A"; // default (unselected) pin colour

// ── Teardrop pin (SDF) ────────────────────────────────────────────────────
// Unclustered listings render as a clean teardrop pin via a MapLibre symbol
// layer fed a runtime-generated SDF icon. SDF (vs a plain raster) is what lets
// `icon-color` recolour the same image at render time (the seam for future
// price/prompt-driven colouring) and gives a crisp white `icon-halo` outline.
// We draw the teardrop with Canvas 2D (smooth bezier silhouette) and convert
// the filled mask into a signed-distance field — far cleaner than hand-rolled
// analytic SDF math.
const PIN_IMAGE_ID = "apt-pin";
const PIN_SCALE = 2; // canvas px per SVG unit (24-unit viewBox → 48px texture)
const PIN_W = 24 * PIN_SCALE;
const PIN_H = 24 * PIN_SCALE;
const SDF_RANGE = 5; // px over which the signed distance spans the alpha ramp

// The Material Design "place" marker — the de-facto standard map pin (round
// head, pointed tip at the bottom), 24×24 SVG viewBox. We use only the OUTER
// teardrop subpath (dropping the icon's centre-hole circle) and fill it solid,
// then turn the mask into an SDF so it stays recolourable via `icon-color`.
const PLACE_PATH =
  "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z";

function drawTeardropMask(): Uint8ClampedArray {
  const canvas = document.createElement("canvas");
  canvas.width = PIN_W;
  canvas.height = PIN_H;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(PIN_SCALE, PIN_SCALE);
  ctx.fillStyle = "#fff";
  ctx.fill(new Path2D(PLACE_PATH));
  return ctx.getImageData(0, 0, PIN_W, PIN_H).data;
}

// Convert the filled mask into an SDF: per pixel, the signed Euclidean distance
// to the shape boundary (negative inside), encoded into alpha around 0.5.
function makeTeardropSDF(): { width: number; height: number; data: Uint8ClampedArray } {
  const w = PIN_W, h = PIN_H;
  const mask = drawTeardropMask();
  const inside = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) inside[i] = mask[i * 4 + 3] > 127 ? 1 : 0;

  // Boundary = inside pixels touching an outside 4-neighbour (the zero level).
  const bx: number[] = [];
  const by: number[] = [];
  const at = (x: number, y: number) =>
    x < 0 || y < 0 || x >= w || y >= h ? 0 : inside[y * w + x];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (!inside[y * w + x]) continue;
      if (!at(x - 1, y) || !at(x + 1, y) || !at(x, y - 1) || !at(x, y + 1)) {
        bx.push(x);
        by.push(y);
      }
    }
  }

  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let min2 = Infinity;
      for (let k = 0; k < bx.length; k++) {
        const dx = x - bx[k], dy = y - by[k];
        const d2 = dx * dx + dy * dy;
        if (d2 < min2) min2 = d2;
      }
      const signed = (inside[y * w + x] ? -1 : 1) * Math.sqrt(min2);
      const alpha = Math.max(0, Math.min(1, 0.5 - signed / (2 * SDF_RANGE)));
      const i = (y * w + x) * 4;
      data[i] = 255;
      data[i + 1] = 255;
      data[i + 2] = 255;
      data[i + 3] = Math.round(alpha * 255);
    }
  }
  return { width: w, height: h, data };
}

// Idempotent — `hasImage` guards the duplicate-add throw, and a style reload
// (e.g. a future Protomaps swap) drops added images so this re-runs on
// `styledata`. Only adds once the style is loaded (addImage requires it).
function ensurePinImage(m: MaplibreGl): void {
  if (!m.isStyleLoaded() || m.hasImage(PIN_IMAGE_ID)) return;
  m.addImage(PIN_IMAGE_ID, makeTeardropSDF(), { sdf: true, pixelRatio: 2 });
}

// ── Transit line badge ("U8") ──────────────────────────────────────────────
// A small coloured rounded-square badge in the BVG network-map idiom, drawn at
// runtime with Canvas 2D (one image per line label, recolour-free since the
// line colour is baked in). Width follows the label so "S41" and "U8" both fit.
function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const BADGE_FONT = "bold 13px Arial, sans-serif";
const BADGE_SCALE = 2; // canvas px per CSS px → crisp at pixelRatio 2

function badgeImageId(label: string): string {
  return `badge-${label}`;
}

// Idempotent (guarded by hasImage); only runs once the style is loaded.
function ensureBadgeImage(m: MaplibreGl, label: string, color: string): void {
  const id = badgeImageId(label);
  if (!m.isStyleLoaded() || m.hasImage(id)) return;

  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = BADGE_FONT;
  const w = Math.ceil(measure.measureText(label).width) + 12;
  const h = 19;

  const canvas = document.createElement("canvas");
  canvas.width = w * BADGE_SCALE;
  canvas.height = h * BADGE_SCALE;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(BADGE_SCALE, BADGE_SCALE);
  roundRect(ctx, 0.5, 0.5, w - 1, h - 1, 3);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.fillStyle = BADGE_TEXT_COLOR;
  ctx.font = BADGE_FONT;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, w / 2, h / 2 + 0.5);

  m.addImage(
    id,
    { width: w * BADGE_SCALE, height: h * BADGE_SCALE, data: ctx.getImageData(0, 0, w * BADGE_SCALE, h * BADGE_SCALE).data },
    { pixelRatio: BADGE_SCALE },
  );
}

// The two ends of a line — where we drop the line badge (BVG maps badge both
// ends of a route). MultiLineString: first vertex of the first part, last of
// the last. Empty for anything that isn't a line.
function lineEndpoints(geom: GeoJSON.Geometry): [number, number][] {
  if (geom.type === "LineString") {
    const c = geom.coordinates;
    return c.length ? [c[0] as [number, number], c[c.length - 1] as [number, number]] : [];
  }
  if (geom.type === "MultiLineString") {
    const parts = geom.coordinates;
    if (!parts.length) return [];
    const first = parts[0];
    const last = parts[parts.length - 1];
    if (!first.length || !last.length) return [];
    return [first[0] as [number, number], last[last.length - 1] as [number, number]];
  }
  return [];
}

// Cluster size always encodes COUNT (more listings → bigger bubble), in both
// the default and lens modes — only the COLOUR changes between them.
const CLUSTER_RADIUS_STEP: ExpressionSpecification = [
  "step",
  ["get", "point_count"],
  16,
  10,
  20,
  30,
  26,
  100,
  34,
];

// Default lens: colour clusters red-by-count (tint → deep), doubling as a
// casual density legend at zoom-out.
const CLUSTER_COUNT_COLOR_STEP: ExpressionSpecification = [
  "step",
  ["get", "point_count"],
  RED_TINT,
  5,
  RED,
  25,
  RED_DEEP,
];

const CLUSTER_LAYER: CircleLayerSpecification = {
  id: "clusters",
  type: "circle",
  source: "apartments",
  filter: ["has", "point_count"],
  paint: {
    "circle-color": CLUSTER_COUNT_COLOR_STEP,
    "circle-opacity": 0.92,
    "circle-radius": CLUSTER_RADIUS_STEP,
    "circle-stroke-color": "#ffffff",
    "circle-stroke-width": 2,
  },
};

// Cluster paint under the active visualization lens ("lens"). A lens
// repaints individual pins but, without this, the cluster bubbles stay
// red-by-count and drown the heatmap at city zoom. So when a lens is active we
// colour each cluster by the MEAN lens value of its members
// (`sum_value / n_valued`, both accumulated via `clusterProperties` on the
// Source), reusing the SAME ramp as the pins. Clusters with no valued member
// (e.g. all-unreachable under a commute lens) fall back to the no-data grey —
// the `n_valued == 0` guard also avoids a divide-by-zero. Size stays count-based.
// Default lens (`price_warm`) → the red-by-count look (unchanged).
function buildClusterPaint(
  lens: MarkerLens | null | undefined,
  domain?: [number, number],
): CircleLayerSpecification["paint"] {
  const meanValue: ExpressionSpecification = [
    "/",
    ["get", "sum_value"],
    ["max", ["get", "n_valued"], 1],
  ];
  const heatmap = rampColorExpression(
    lens,
    meanValue,
    ["==", ["get", "n_valued"], 0] as ExpressionSpecification,
    domain,
  );
  return {
    "circle-color": heatmap ?? CLUSTER_COUNT_COLOR_STEP,
    "circle-opacity": 0.92,
    "circle-radius": CLUSTER_RADIUS_STEP,
    "circle-stroke-color": "#ffffff",
    "circle-stroke-width": 2,
  };
}

const CLUSTER_COUNT_LAYER: SymbolLayerSpecification = {
  id: "cluster-count",
  type: "symbol",
  source: "apartments",
  filter: ["has", "point_count"],
  layout: {
    "text-field": "{point_count_abbreviated}",
    "text-size": 12,
    "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
    "text-letter-spacing": 0.04,
  },
  paint: {
    "text-color": "#ffffff",
  },
};

// The apartment pin — a single clean teardrop, recoloured by state. No glow
// layer (it read as a muddy blob); default grey, hover red. A crisp white halo
// outlines every pin for legibility against the map. The SELECTED listing is
// NOT styled here — it gets its own always-on-top pin (ACTIVE_PIN_LAYER) so it
// shows even when this layer's feature is swallowed by a cluster. Same layer id
// ("unclustered-point") as the old circle so click/hover wiring +
// interactiveLayerIds need no change.
const PIN_LAYER: SymbolLayerSpecification = {
  id: "unclustered-point",
  type: "symbol",
  source: "apartments",
  filter: ["!", ["has", "point_count"]],
  layout: {
    "icon-image": PIN_IMAGE_ID,
    "icon-anchor": "bottom",
    "icon-allow-overlap": true,
    "icon-ignore-placement": true,
    "icon-size": 1.2,
  },
  paint: {
    "icon-color": [
      "case",
      ["boolean", ["feature-state", "hover"], false],
      RED,
      GREY,
    ],
    "icon-halo-color": "#ffffff",
    "icon-halo-width": 1.2,
    "icon-color-transition": { duration: 140, delay: 0 },
  },
};

// Pin paint for the active visualization lens. Default lens (`price_warm`)
// → today's grey/hover look; a ramped lens (commute) → heatmap over
// `lens_value`, hover still wins for affordance. Overrides PIN_LAYER.paint at
// render time so the colour follows `marker_lens`.
function buildPinPaint(
  lens: MarkerLens | null | undefined,
  domain?: [number, number],
): SymbolLayerSpecification["paint"] {
  return {
    "icon-color": [
      "case",
      ["boolean", ["feature-state", "hover"], false],
      RED,
      lensColorExpression(lens, GREY, domain),
    ],
    "icon-halo-color": "#ffffff",
    "icon-halo-width": 1.2,
    "icon-color-transition": { duration: 140, delay: 0 },
  };
}

// Number label over each declustered pin under an active heatmap lens (e.g. the
// commute minutes). Mirrors CLUSTER_COUNT_LAYER's text approach. The value is
// pre-formatted into `lens_label` on the feature (empty string when there's no
// lens / no value → nothing drawn). `text-allow-overlap: false` declutters at
// dense zooms; the label sits just above the teardrop tip. Recoloured per-pin to
// match the heatmap so the number reads as part of the same encoding.
const PIN_LABEL_LAYER: SymbolLayerSpecification = {
  id: "unclustered-point-label",
  type: "symbol",
  source: "apartments",
  filter: ["!", ["has", "point_count"]],
  layout: {
    "text-field": ["get", "lens_label"],
    "text-size": 11,
    "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
    "text-anchor": "bottom",
    "text-offset": [0, -2.1],
    "text-allow-overlap": false,
    "text-optional": true,
  },
  paint: {
    "text-color": "#1b1b1b",
    "text-halo-color": "#ffffff",
    "text-halo-width": 1.6,
  },
};

// The SELECTED listing's pin — its own single-feature, UNCLUSTERED source drawn
// on top of everything. This is what fixes "selecting a listing shows the
// cluster bubble, not a pin": the clustered `unclustered-point` layer has no
// feature to highlight when the selection sits inside a cluster, so we render
// the active one separately. Bigger + brand-red + thicker halo so it reads as
// the focus, floating above any cluster it overlaps. Purely visual — kept OUT
// of interactiveLayerIds so clicks fall through to the cluster/point beneath.
const ACTIVE_PIN_LAYER: SymbolLayerSpecification = {
  id: "active-point",
  type: "symbol",
  source: "active-apartment",
  layout: {
    "icon-image": PIN_IMAGE_ID,
    "icon-anchor": "bottom",
    "icon-allow-overlap": true,
    "icon-ignore-placement": true,
    "icon-size": 1.6,
  },
  paint: {
    "icon-color": RED,
    "icon-halo-color": "#ffffff",
    "icon-halo-width": 2.5,
  },
};

// How long the marker/cluster layers fade in when a new result set lands (ms).
const FADE_MS = 320;

// Resolve the [lng, lat] for the selected listing: prefer its marker in the
// active result set, fall back to the tier-3 detail blob (covers an agent
// open_listing for an id outside the current results). Shared by the pan
// effect and the active-pin overlay so they always agree on the location.
function resolveActiveCenter(
  activeId: string | null,
  coordsById: Map<string, [number, number]>,
  detail: { latitude: number | null; longitude: number | null } | null | undefined,
): [number, number] | null {
  if (!activeId) return null;
  const fromMarkers = coordsById.get(activeId);
  if (fromMarkers) return fromMarkers;
  if (detail && detail.latitude != null && detail.longitude != null) {
    return [detail.longitude, detail.latitude];
  }
  return null;
}

export function MapPane() {
  // Click + hover handlers live at the MapLibreMap level so we use
  // react-maplibre's React event dispatch path — far more reliable than
  // raw `m.on('click', ...)` listeners attached via useEffect, which
  // suffer from closure / re-attach races against CopilotKit's frequent
  // state updates. `interactiveLayerIds` populates `e.features` with the
  // hits at the click/move point, so we don't need queryRenderedFeatures.
  const { activate } = useSessionState();
  const { setHover } = useHover();

  // The real maplibre Map instance, captured on load. We pass it down to
  // ApartmentLayer rather than relying on `useMap()`, which returns undefined
  // for the keyed map here (no <MapProvider> in the tree) — that left every
  // imperative effect (pin image, hover/active highlight, pan) bailing.
  const [mapInstance, setMapInstance] = useState<MaplibreGl | null>(null);

  // Guards the one-time `styleimagemissing` registration. `onLoad` can fire
  // again on a style reload (e.g. a future Protomaps swap), and we must not
  // stack a fresh listener each time.
  const pinHandlerBound = useRef(false);

  // Stable refs so the handler closures always see the LATEST activate /
  // setHover without needing to re-bind on every render. React's useState
  // setters are stable; CopilotKit-derived ones may not be.
  const activateRef = useRef(activate);
  activateRef.current = activate;
  const setHoverRef = useRef(setHover);
  setHoverRef.current = setHover;

  const onMapClick = useCallback((e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) return;
    const map = e.target;

    if (f.layer.id === "clusters") {
      const clusterId = f.properties?.cluster_id;
      const source = map.getSource("apartments") as GeoJSONSource;
      source
        .getClusterExpansionZoom(clusterId)
        .then((zoom: number) => {
          const geom = f.geometry as Point;
          map.easeTo({
            center: geom.coordinates as [number, number],
            zoom,
            duration: 350,
          });
        })
        .catch(() => {
          // Best-effort — cluster could re-cluster between hit and resolve.
        });
      return;
    }

    // Unclustered apartment dot — open detail in cards pane. Goes through
    // the activate() helper so the HTTP detail fetch fires alongside the
    // active_id update.
    const id = f.properties?.id ?? (f.id as string | undefined);
    if (id) {
      void activateRef.current(String(id));
    }
  }, []);

  const onMapMouseMove = useCallback((e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    const id = f?.properties?.id ?? (f?.id as string | undefined);
    setHoverRef.current(id ? String(id) : null);
    e.target.getCanvas().style.cursor = f ? "pointer" : "";
  }, []);

  const onMapMouseLeave = useCallback((e: MapLayerMouseEvent) => {
    setHoverRef.current(null);
    e.target.getCanvas().style.cursor = "";
  }, []);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
    <MapLibreMap
      id="apartments-map"
      initialViewState={BERLIN_CENTER}
      mapStyle={MAP_STYLE_URL}
      style={{ width: "100%", height: "100%" }}
      // Disable the built-in attribution so we can add ours once, carrying the
      // ODbL obligation for the OSM-sourced landmark data alongside the
      // basemap's own credits.
      attributionControl={false}
      interactiveLayerIds={["unclustered-point", "clusters"]}
      maxBounds={BERLIN_BOUNDS}
      minZoom={9}
      maxZoom={18}
      onClick={onMapClick}
      onMouseMove={onMapMouseMove}
      onMouseLeave={onMapMouseLeave}
      // MapLibre's default wheel-zoom rate (1/450) feels sluggish for an
      // apartment-search map where users zoom in/out a lot. Crank it up.
      onLoad={(e) => {
        const m = e.target;
        m.scrollZoom.setWheelZoomRate(1 / 90);
        m.scrollZoom.setZoomRate(1 / 100);
        // Register the SDF pin eagerly, and lazily via `styleimagemissing` —
        // the latter fires the moment a layer references `apt-pin` without it,
        // so the pins render regardless of style-load / layer-mount ordering
        // (and re-register after a style swap). This replaces a fragile
        // styledata/isStyleLoaded gate that left the image unregistered.
        ensurePinImage(m);
        if (!pinHandlerBound.current) {
          pinHandlerBound.current = true;
          m.on("styleimagemissing", (ev: { id: string }) => {
            if (ev.id === PIN_IMAGE_ID) ensurePinImage(m);
          });
        }
        setMapInstance(m);
      }}
    >
      {/* ODbL obligation: landmark data is partly sourced from OpenStreetMap.
          `compact` keeps the credit to an "i" toggle so it doesn't crowd the
          map; the basemap's own attribution still rides the same control. */}
      <AttributionControl
        compact
        customAttribution="© OpenStreetMap contributors"
      />
      {/* Overlays render BEFORE the apartment pins so markers stay on top. */}
      <OverlayLayer map={mapInstance} />
      <ApartmentLayer map={mapInstance} />
    </MapLibreMap>
      <OverlayLegend />
      <LensLegend />
    </div>
  );
}

// Overlays are drawn BENEATH the apartment layers: inserting every overlay
// layer `beforeId` the bottom-most apartment layer ("clusters") keeps them under
// the pins/clusters regardless of mount timing. (Declarative child order alone
// doesn't guarantee this — overlays mount when search state arrives, AFTER the
// apartment layers, so without beforeId MapLibre would stack them on top.)
const OVERLAY_BEFORE_ID = "clusters";

// Render every agent-drawn geometry in `state.map_overlays`. Appearance is
// resolved from `overlayStyles` by (kind, geometry type) — the backend only
// supplied semantics. Hybrid treatment: area overlays (polygons) get a soft
// blurred halo + translucent fill + outline; lines get a crisp stroke plus a
// flowing dash shimmer; transit lines additionally get station dots + line
// badges. A single rAF loop (below) breathes the halos, marches the dashes, and
// pulses the stations — all gated on prefers-reduced-motion, with the
// declarative paints here serving as the static fallback.
function OverlayLayer({ map }: { map: MaplibreGl | null }) {
  const { state } = useSessionState();
  const overlays = state?.map_overlays ?? [];

  // Latest overlays for the imperative loop without re-subscribing every frame.
  const overlaysRef = useRef(overlays);
  overlaysRef.current = overlays;

  // Stable signature of the overlay SET so the imperative effects below re-arm
  // only when an overlay is added/removed — not on every render (`state?.
  // map_overlays ?? []` is a fresh array each time). "" ⇒ nothing drawn.
  const overlayKey = useMemo(() => overlays.map((o) => o.id).join("|"), [overlays]);

  // Register a badge image per transit line label (idempotent). addImage needs a
  // loaded style; if the style isn't ready when overlays first arrive (cold map +
  // an immediate transit overlay) `ensureBadgeImage` no-ops, so we also retry on
  // `styledata` — otherwise the `-badge` symbol layer would reference a missing
  // icon-image until the overlay set next changes.
  useEffect(() => {
    if (!map) return;
    const register = () => {
      for (const o of overlaysRef.current) {
        if (o.kind === "transit_line") {
          ensureBadgeImage(map, o.label, overlayColor(o, "line"));
        }
      }
    };
    register();
    map.on("styledata", register);
    return () => {
      map.off("styledata", register);
    };
  }, [map, overlayKey]);

  // One rAF loop drives all motion. Skipped entirely when nothing is drawn (no
  // idle 60fps wakeup on an empty map) or under prefers-reduced-motion (the
  // declarative paints stand as the static look). Reads geometry from
  // overlaysRef; guards every set with getLayer so a layer mid-mount/unmount is
  // a no-op. Re-arms on `overlayKey` so it starts when the first overlay appears.
  const entranceStarts = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    if (!map || overlayKey === "") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    // Drop entrance timers for overlays no longer drawn so the Map doesn't grow
    // unbounded over a long session.
    const liveIds = new Set(overlaysRef.current.map((o) => o.id));
    for (const id of [...entranceStarts.current.keys()]) {
      if (!liveIds.has(id)) entranceStarts.current.delete(id);
    }

    let raf = 0;
    let prevFlowStep = -1;
    const set = (layer: string, prop: string, val: unknown) => {
      if (map.getLayer(layer)) map.setPaintProperty(layer, prop, val);
    };

    const frame = (now: number) => {
      const breath =
        0.5 - 0.5 * Math.cos((2 * Math.PI * now) / OVERLAY_BREATH_PERIOD_MS);
      const pulse =
        0.5 - 0.5 * Math.cos((2 * Math.PI * now) / STATION_PULSE_PERIOD_MS);
      const flowStep =
        Math.floor(now / OVERLAY_FLOW_STEP_MS) % FLOW_DASH_SEQUENCE.length;
      const flowChanged = flowStep !== prevFlowStep;
      prevFlowStep = flowStep;

      for (const o of overlaysRef.current) {
        const src = `overlay-${o.id}`;
        let start = entranceStarts.current.get(o.id);
        if (start === undefined) {
          start = now;
          entranceStarts.current.set(o.id, now);
        }
        const raw = Math.min(1, (now - start) / OVERLAY_ENTRANCE_MS);
        const p = 1 - (1 - raw) * (1 - raw); // easeOutQuad fade-in

        const shape = overlayShape(o.geojson);
        if (shape === "fill") {
          const halo =
            (OVERLAY_HALO_OPACITY_MIN +
              (OVERLAY_HALO_OPACITY_MAX - OVERLAY_HALO_OPACITY_MIN) * breath) *
            p;
          const fill =
            (OVERLAY_FILL_OPACITY_MIN +
              (OVERLAY_FILL_OPACITY_MAX - OVERLAY_FILL_OPACITY_MIN) * breath) *
            p;
          set(`${src}-halo`, "line-opacity", halo);
          set(`${src}-fill`, "fill-opacity", fill);
          set(`${src}-outline`, "line-opacity", 0.8 * p);
        } else if (shape === "line") {
          set(`${src}-line`, "line-opacity", OVERLAY_LINE_OPACITY * p);
          if (overlayLineFlows(o, shape)) {
            set(`${src}-flow`, "line-opacity", OVERLAY_FLOW_OPACITY * p);
            if (flowChanged) {
              set(`${src}-flow`, "line-dasharray", FLOW_DASH_SEQUENCE[flowStep]);
            }
          }
          // Station aura expands + fades as it grows; dot + badge just fade in.
          set(
            `${src}-aura`,
            "circle-radius",
            STATION_AURA_RADIUS_MIN +
              (STATION_AURA_RADIUS_MAX - STATION_AURA_RADIUS_MIN) * pulse,
          );
          set(`${src}-aura`, "circle-opacity", STATION_AURA_OPACITY * (1 - pulse) * p);
          set(`${src}-dot`, "circle-opacity", p);
          set(`${src}-dot`, "circle-stroke-opacity", p);
          set(`${src}-badge`, "icon-opacity", p);
        } else {
          set(`${src}-point`, "circle-opacity", 0.85 * p);
          set(`${src}-point`, "circle-stroke-opacity", p);
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [map, overlayKey]);

  return (
    <>
      {overlays.map((o) => {
        const shape = overlayShape(o.geojson);
        const color = overlayColor(o, shape);
        const srcId = `overlay-${o.id}`;
        const data: FeatureCollection = {
          type: "FeatureCollection",
          features: [
            { type: "Feature", geometry: o.geojson, properties: {} } as Feature,
          ],
        };

        if (shape === "fill") {
          return (
            <Source key={srcId} id={srcId} type="geojson" data={data}>
              {/* Blurred boundary halo — the breathing target. Drawn first so
                  it sits beneath the fill/outline. */}
              <Layer
                id={`${srcId}-halo`}
                type="line"
                beforeId={OVERLAY_BEFORE_ID}
                layout={{ "line-cap": "round", "line-join": "round" }}
                paint={{
                  "line-color": color,
                  "line-width": OVERLAY_HALO_WIDTH,
                  "line-blur": OVERLAY_HALO_BLUR,
                  "line-opacity": OVERLAY_HALO_OPACITY_MAX,
                }}
              />
              <Layer
                id={`${srcId}-fill`}
                type="fill"
                beforeId={OVERLAY_BEFORE_ID}
                paint={{ "fill-color": color, "fill-opacity": OVERLAY_FILL_OPACITY }}
              />
              <Layer
                id={`${srcId}-outline`}
                type="line"
                beforeId={OVERLAY_BEFORE_ID}
                paint={{
                  "line-color": color,
                  "line-width": OVERLAY_OUTLINE_WIDTH,
                  "line-opacity": 0.8,
                }}
              />
            </Source>
          );
        }

        if (shape === "line") {
          const flows = overlayLineFlows(o, shape);
          const points = o.points ?? [];
          const stationData: FeatureCollection = {
            type: "FeatureCollection",
            features: points.map((pt) => ({
              type: "Feature",
              geometry: { type: "Point", coordinates: [pt.lon, pt.lat] },
              properties: { label: pt.label },
            })),
          };
          const ends = lineEndpoints(o.geojson);
          const badgeData: FeatureCollection = {
            type: "FeatureCollection",
            features: ends.map((c) => ({
              type: "Feature",
              geometry: { type: "Point", coordinates: c },
              properties: {},
            })),
          };

          return (
            <Fragment key={srcId}>
              <Source id={srcId} type="geojson" data={data}>
                <Layer
                  id={`${srcId}-line`}
                  type="line"
                  beforeId={OVERLAY_BEFORE_ID}
                  layout={{ "line-cap": "round", "line-join": "round" }}
                  paint={{
                    "line-color": color,
                    "line-width": OVERLAY_LINE_WIDTH,
                    "line-opacity": OVERLAY_LINE_OPACITY,
                  }}
                />
                {flows && (
                  <Layer
                    id={`${srcId}-flow`}
                    type="line"
                    beforeId={OVERLAY_BEFORE_ID}
                    layout={{ "line-cap": "butt", "line-join": "round" }}
                    paint={{
                      "line-color": OVERLAY_FLOW_COLOR,
                      "line-width": OVERLAY_FLOW_WIDTH,
                      "line-opacity": OVERLAY_FLOW_OPACITY,
                      "line-dasharray": FLOW_DASH_SEQUENCE[0],
                    }}
                  />
                )}
              </Source>
              {points.length > 0 && (
                <Source id={`${srcId}-stations`} type="geojson" data={stationData}>
                  <Layer
                    id={`${srcId}-aura`}
                    type="circle"
                    beforeId={OVERLAY_BEFORE_ID}
                    paint={{
                      "circle-color": color,
                      "circle-radius": STATION_AURA_RADIUS_MIN,
                      "circle-opacity": STATION_AURA_OPACITY,
                      "circle-blur": 0.6,
                    }}
                  />
                  <Layer
                    id={`${srcId}-dot`}
                    type="circle"
                    beforeId={OVERLAY_BEFORE_ID}
                    paint={{
                      "circle-color": STATION_FILL,
                      "circle-radius": STATION_RADIUS,
                      "circle-stroke-color": color,
                      "circle-stroke-width": STATION_STROKE_WIDTH,
                    }}
                  />
                </Source>
              )}
              {ends.length > 0 && o.kind === "transit_line" && (
                <Source id={`${srcId}-badges`} type="geojson" data={badgeData}>
                  <Layer
                    id={`${srcId}-badge`}
                    type="symbol"
                    beforeId={OVERLAY_BEFORE_ID}
                    layout={{
                      "icon-image": badgeImageId(o.label),
                      "icon-size": 1,
                      "icon-allow-overlap": true,
                    }}
                  />
                </Source>
              )}
            </Fragment>
          );
        }

        return (
          <Source key={srcId} id={srcId} type="geojson" data={data}>
            <Layer
              id={`${srcId}-point`}
              type="circle"
              beforeId={OVERLAY_BEFORE_ID}
              paint={{
                "circle-color": color,
                "circle-radius": OVERLAY_POINT_RADIUS,
                "circle-opacity": 0.85,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 2,
              }}
            />
          </Source>
        );
      })}
    </>
  );
}

function ApartmentLayer({ map }: { map: MaplibreGl | null }) {
  const { state } = useSessionState();
  const { hoverId, activeId: clientActiveId } = useHover();
  const lastFeatureStateIds = useRef<Set<string>>(new Set());
  const lastPannedId = useRef<string | null>(null);

  // Mirror the authoritative SessionState.active_id into the hover store so the
  // agent path (open_listing / reload hydration, which arrive as SSE deltas and
  // never run through activate()) updates the same client-local selection a
  // card click does. Without this, a stale click would mask a later agent
  // selection. See useActiveIdMirror + useHover's comment.
  useActiveIdMirror(state?.active_id ?? null);

  // Selected listing for the map. The mirror above keeps `clientActiveId` in
  // sync with both paths; the `?? state.active_id` fallback only covers the
  // first-paint/hydration window before the mirror effect runs.
  const activeId = clientActiveId ?? state?.active_id ?? null;

  // The decoded result set — the single source for the geojson, the id→coord
  // lookup, and the result-set fingerprint below. (decodeMarkers is cheap, but
  // doing it once keeps the three derivations in lockstep.)
  const markers = useMemo(
    () => decodeMarkers(state?.result_markers),
    [state?.result_markers],
  );

  // Cheap, stable fingerprint of the result set: length + first/last id. A new
  // search yields a different signature; the same set echoed across turns keeps
  // the same one even though `result_markers` is a fresh reference each
  // snapshot. Drives the fade + reframe so they fire on a genuine new result
  // set, not on every state delta (and NOT on pagination, which leaves
  // `result_markers` untouched). Mirrors CardStrip's markersSig.
  const markersSig = useMemo(
    () =>
      `${markers.length}:${markers[0]?.id ?? ""}:${markers[markers.length - 1]?.id ?? ""}`,
    [markers],
  );

  const lens = state?.marker_lens;

  // Adaptive ramp domain from the actual lens values (frontend-computed — the
  // markers carry every value already). Stretches the ramp to the real spread
  // so contrast tracks the data instead of the wide native window. `undefined`
  // for the default (non-heatmap) lens.
  const lensDomainValue = useMemo(
    () => lensDomain(markers.map((m) => m.lens_value), lens),
    [markers, lens],
  );

  // Compact per-pin number label ("24") under an active heatmap lens. Pre-format
  // here (not in MapLibre) so it follows the lens's own formatter; empty string
  // → no label drawn (default lens, or a null/unreachable value).
  const style = lensStyle(lens);
  const geojson = useMemo<FeatureCollection<Point, ApartmentProps>>(() => {
    const features = markers.map((m) => ({
      type: "Feature" as const,
      id: m.id,
      geometry: { type: "Point" as const, coordinates: [m.lng, m.lat] },
      properties: {
        id: m.id,
        // The active visualization lens's scalar — drives the pin heatmap
        // when a non-default lens (e.g. commute) is active. `null` renders
        // in the lens's "no data" colour.
        lens_value: m.lens_value,
        lens_label:
          style && typeof m.lens_value === "number"
            ? String(Math.round(m.lens_value))
            : "",
      },
    }));
    return { type: "FeatureCollection", features };
  }, [markers, style]);

  // Pin paint follows the active visualization lens (grey/hover by default;
  // a commute heatmap once a travel lens is applied), stretched to the adaptive
  // domain.
  const pinPaint = useMemo(
    () => buildPinPaint(lens, lensDomainValue),
    [lens, lensDomainValue],
  );

  // Cluster paint follows the SAME lens — red-by-count by default, mean-lens
  // heatmap when a lens is active (so the bubbles don't drown the pin heatmap at
  // city zoom).
  const clusterPaint = useMemo(
    () => buildClusterPaint(lens, lensDomainValue),
    [lens, lensDomainValue],
  );

  // id → [lng, lat] for the active result set, so a card/pin click can pan
  // the map to the selected listing.
  const coordsById = useMemo(() => {
    const m = new Map<string, [number, number]>();
    for (const mk of markers) {
      m.set(mk.id, [mk.lng, mk.lat]);
    }
    return m;
  }, [markers]);

  // Single-feature collection for the always-on-top active pin. Empty when
  // nothing is selected (or its location can't be resolved yet).
  const activeGeojson = useMemo<FeatureCollection<Point, ApartmentProps>>(() => {
    const center = resolveActiveCenter(
      activeId,
      coordsById,
      state?.active_listing_detail,
    );
    if (!activeId || !center) {
      return { type: "FeatureCollection", features: [] };
    }
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          id: activeId,
          geometry: { type: "Point", coordinates: center },
          properties: { id: activeId, lens_value: null, lens_label: "" },
        },
      ],
    };
  }, [activeId, coordsById, state?.active_listing_detail]);

  // Pan (and gently zoom in only when far out) to the active listing whenever
  // the selection changes. The highlight is the dedicated active-pin layer.
  useEffect(() => {
    if (!activeId) {
      lastPannedId.current = null; // allow re-pan if the same id is re-selected
      return;
    }
    if (activeId === lastPannedId.current) return;
    const m = map;
    if (!m) return;
    const center = resolveActiveCenter(
      activeId,
      coordsById,
      state?.active_listing_detail,
    );
    if (!center) return;
    lastPannedId.current = activeId;
    const opts: { center: [number, number]; duration: number; zoom?: number } = {
      center,
      duration: 850,
    };
    if (m.getZoom() < 12.5) opts.zoom = 14; // gentle zoom-in only when zoomed far out
    m.easeTo(opts);
  }, [map, activeId, state?.active_listing_detail, coordsById]);

  // Drive HOVER visual state by setFeatureState. (Active selection is rendered
  // by the dedicated active-pin layer, not feature-state, so it shows even when
  // clustered.) Track the id we touched last frame so we can clean it up —
  // feature-state persists until explicitly reset.
  useEffect(() => {
    const m = map;
    if (!m) return;
    const src = "apartments";
    const prev = lastFeatureStateIds.current;
    for (const id of prev) {
      if (id !== hoverId) m.removeFeatureState({ source: src, id });
    }
    const next = new Set<string>();
    if (hoverId) {
      m.setFeatureState({ source: src, id: hoverId }, { hover: true });
      next.add(hoverId);
    }
    lastFeatureStateIds.current = next;
  }, [map, hoverId, state?.result_markers]);

  // New result set → fade the marker + cluster layers in, and reframe the
  // camera if warranted. Both fire on `markersSig` (a genuine new/refined set),
  // skipping the first mount. The reframe rule lives in mapCamera.shouldReframe.
  const didMountResults = useRef(false);
  useEffect(() => {
    const m = map;
    if (!m) return;
    if (!didMountResults.current) {
      didMountResults.current = true;
      return; // first paint: no fade / no reframe
    }

    // ── Fade-in: kill the transition, snap each layer's opacity to 0, then on
    // the next frame restore the target with a transition. The layers persist
    // across the data swap, so the paint transition animates the new features.
    const fades: Array<[string, string, number]> = [
      ["unclustered-point", "icon-opacity", 1],
      ["clusters", "circle-opacity", 0.92],
      ["cluster-count", "text-opacity", 1],
    ];
    for (const [layer, prop] of fades) {
      if (!m.getLayer(layer)) continue;
      m.setPaintProperty(layer, `${prop}-transition`, { duration: 0, delay: 0 });
      m.setPaintProperty(layer, prop, 0);
    }
    const raf = requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        for (const [layer, prop, target] of fades) {
          if (!m.getLayer(layer)) continue;
          m.setPaintProperty(layer, `${prop}-transition`, {
            duration: FADE_MS,
            delay: 0,
          });
          m.setPaintProperty(layer, prop, target);
        }
      }),
    );

    // ── Reframe (gated): never while a listing is selected; otherwise when
    // zoomed out or the results aren't on screen. See mapCamera.shouldReframe.
    const bbox = markersBBox(markers);
    if (bbox) {
      const b = m.getBounds();
      const fractionInView = fractionInside(markers, {
        west: b.getWest(),
        south: b.getSouth(),
        east: b.getEast(),
        north: b.getNorth(),
      });
      if (
        shouldReframe({
          zoom: m.getZoom(),
          fractionInView,
          // Read the authoritative snapshot field, NOT the `activeId` mirror —
          // the hover-store mirror lags one render behind a state delta, so on
          // the commit where new markers arrive it still holds the OLD
          // selection and would wrongly suppress the reframe. `state.active_id`
          // is correct in this same snapshot (a new search clears it; an agent
          // open_listing in the same turn sets it → then we DO defer to it).
          hasActiveSelection: !!state?.active_id,
          markerCount: markers.length,
        })
      ) {
        m.fitBounds(bbox, {
          padding: 60,
          maxZoom: REFRAME_MAX_ZOOM,
          duration: REFRAME_MS,
        });
      }
    }

    return () => cancelAnimationFrame(raf);
    // Keyed on markersSig (the result-set fingerprint), not `markers` identity,
    // so it fires once per genuine change. activeId is read but intentionally
    // not a dep — we want the selection state AT result-change time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, markersSig]);

  return (
    <>
      <Source
        id="apartments"
        type="geojson"
        data={geojson}
        cluster
        clusterRadius={50}
        clusterMaxZoom={14}
        promoteId="id"
        // Accumulate the active lens scalar per cluster so the bubble can be
        // coloured by its members' MEAN value (sum/n). `lens_value` is null
        // for markers with no value in the active lens → counted as neither sum
        // nor n, so the mean reflects only reachable/valued members.
        clusterProperties={{
          sum_value: [
            "+",
            [
              "case",
              ["==", ["get", "lens_value"], null],
              0,
              ["get", "lens_value"],
            ],
          ],
          n_valued: [
            "+",
            ["case", ["==", ["get", "lens_value"], null], 0, 1],
          ],
        }}
      >
        <Layer {...CLUSTER_LAYER} paint={clusterPaint} />
        <Layer {...CLUSTER_COUNT_LAYER} />
        <Layer {...PIN_LAYER} paint={pinPaint} />
        <Layer {...PIN_LABEL_LAYER} />
      </Source>
      {/* Declared after the clustered source so the active pin draws on top. */}
      <Source id="active-apartment" type="geojson" data={activeGeojson}>
        <Layer {...ACTIVE_PIN_LAYER} />
      </Source>
    </>
  );
}

interface ApartmentProps {
  id: string;
  lens_value: number | null;
  lens_label: string;
}
