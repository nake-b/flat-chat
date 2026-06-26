import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Map as MapLibreMap, Source, Layer } from "@vis.gl/react-maplibre";
import type {
  CircleLayerSpecification,
  GeoJSONSource,
  Map as MaplibreGl,
  MapLayerMouseEvent,
  SymbolLayerSpecification,
} from "maplibre-gl";
import type { FeatureCollection, Point } from "geojson";

import { useSessionState } from "../hooks/useSessionState";
import { useHover } from "../hooks/useHover";
import { decodeMarkers } from "../state/SessionState";

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
const INK = "#0A0A0A";
const GREY = "#5A5A5A"; // default (unselected) pin colour

// ── Teardrop pin (SDF) ────────────────────────────────────────────────────
// Unclustered listings render as teardrop pins via a MapLibre symbol layer
// fed a runtime-generated SDF icon. SDF (vs a plain raster) is what lets
// `icon-color` recolour the same image to ARBITRARY colours at render time —
// the seam for future price/prompt-driven colouring. We generate the SDF in
// JS (no committed binary asset): the teardrop is an analytic shape (head
// circle ∪ tip triangle), so its signed-distance field is exact and cheap.
const PIN_IMAGE_ID = "apt-pin";
const PIN_W = 48;
const PIN_H = 64;
const SDF_RANGE = 8; // px over which the signed distance spans the alpha ramp

function dist(ax: number, ay: number, bx: number, by: number): number {
  return Math.hypot(ax - bx, ay - by);
}

// Signed distance to a triangle — Inigo Quilez's 2D primitive, ported to JS.
// Negative inside, positive outside. Used unioned (min) with the head circle.
function sdTriangle(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number,
  cx: number, cy: number,
): number {
  const e0x = bx - ax, e0y = by - ay;
  const e1x = cx - bx, e1y = cy - by;
  const e2x = ax - cx, e2y = ay - cy;
  const v0x = px - ax, v0y = py - ay;
  const v1x = px - bx, v1y = py - by;
  const v2x = px - cx, v2y = py - cy;
  const cl = (n: number) => Math.max(0, Math.min(1, n));
  const t0 = cl((v0x * e0x + v0y * e0y) / (e0x * e0x + e0y * e0y));
  const t1 = cl((v1x * e1x + v1y * e1y) / (e1x * e1x + e1y * e1y));
  const t2 = cl((v2x * e2x + v2y * e2y) / (e2x * e2x + e2y * e2y));
  const p0x = v0x - e0x * t0, p0y = v0y - e0y * t0;
  const p1x = v1x - e1x * t1, p1y = v1y - e1y * t1;
  const p2x = v2x - e2x * t2, p2y = v2y - e2y * t2;
  const s = Math.sign(e0x * e2y - e0y * e2x);
  // vec2 min by .x (squared distance), carrying .y (signed cross product).
  let dx = p0x * p0x + p0y * p0y;
  let dy = s * (v0x * e0y - v0y * e0x);
  const d1x = p1x * p1x + p1y * p1y;
  const d1y = s * (v1x * e1y - v1y * e1x);
  if (d1x < dx) { dx = d1x; dy = d1y; }
  const d2x = p2x * p2x + p2y * p2y;
  const d2y = s * (v2x * e2y - v2y * e2x);
  if (d2x < dx) { dx = d2x; dy = d2y; }
  return -Math.sqrt(dx) * Math.sign(dy);
}

function makeTeardropSDF(): { width: number; height: number; data: Uint8ClampedArray } {
  const w = PIN_W, h = PIN_H;
  const data = new Uint8ClampedArray(w * h * 4);
  const cx = w / 2;
  const r = w / 2 - 6; // head radius
  const cy = r + 3; // head centre near the top
  const tipX = w / 2;
  const tipY = h - 2; // tip near the bottom → icon-anchor "bottom" lands on the point
  const a = 0.9; // splay of the triangle's top verts on the circle
  const lx = cx - r * Math.sin(a), ly = cy + r * Math.cos(a);
  const rx = cx + r * Math.sin(a), ry = cy + r * Math.cos(a);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px = x + 0.5, py = y + 0.5;
      const dCircle = dist(px, py, cx, cy) - r;
      const dTri = sdTriangle(px, py, lx, ly, rx, ry, tipX, tipY);
      const d = Math.min(dCircle, dTri); // union of head + tip
      const alpha = Math.max(0, Math.min(1, 0.5 - d / (2 * SDF_RANGE)));
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

const CLUSTER_LAYER: CircleLayerSpecification = {
  id: "clusters",
  type: "circle",
  source: "apartments",
  filter: ["has", "point_count"],
  paint: {
    "circle-color": [
      "step",
      ["get", "point_count"],
      RED_TINT,
      5,
      RED,
      25,
      RED_DEEP,
    ],
    "circle-opacity": 0.92,
    "circle-radius": [
      "step",
      ["get", "point_count"],
      16,
      10,
      20,
      30,
      26,
      100,
      34,
    ],
    "circle-stroke-color": "#ffffff",
    "circle-stroke-width": 2,
  },
};

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

// Soft red glow behind a selected pin — same SDF teardrop, larger, drawn
// first so the pin sits on top. Fades in only when feature-state.active.
// NOTE: feature-state works in PAINT props only (not layout), so the size is
// fixed and we toggle visibility via `icon-opacity` (paint), not icon-size.
const PIN_HALO_LAYER: SymbolLayerSpecification = {
  id: "unclustered-point-halo",
  type: "symbol",
  source: "apartments",
  filter: ["!", ["has", "point_count"]],
  layout: {
    "icon-image": PIN_IMAGE_ID,
    "icon-anchor": "bottom",
    "icon-allow-overlap": true,
    "icon-ignore-placement": true,
    "icon-size": 1.55,
  },
  paint: {
    "icon-color": RED,
    "icon-opacity": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      0.28,
      0,
    ],
    "icon-opacity-transition": { duration: 220, delay: 0 },
  },
};

// The apartment pin. Same layer id ("unclustered-point") as the old circle so
// click/hover wiring + interactiveLayerIds need no change. Active = ink,
// hover = red, default = grey, recoloured via `icon-color` (paint → can read
// feature-state). icon-size is fixed (layout can't read feature-state); the
// emphasis comes from colour + a thicker white halo + the glow layer above.
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
    "icon-size": 1.0,
  },
  paint: {
    "icon-color": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      INK,
      ["boolean", ["feature-state", "hover"], false],
      RED,
      GREY,
    ],
    "icon-halo-color": "#ffffff",
    "icon-halo-width": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      2,
      ["boolean", ["feature-state", "hover"], false],
      1.8,
      1.2,
    ],
    "icon-color-transition": { duration: 140, delay: 0 },
  },
};

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
    <MapLibreMap
      id="apartments-map"
      initialViewState={BERLIN_CENTER}
      mapStyle={MAP_STYLE_URL}
      style={{ width: "100%", height: "100%" }}
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
        m.on("styleimagemissing", (ev: { id: string }) => {
          if (ev.id === PIN_IMAGE_ID) ensurePinImage(m);
        });
        setMapInstance(m);
      }}
    >
      <ApartmentLayer map={mapInstance} />
    </MapLibreMap>
  );
}

function ApartmentLayer({ map }: { map: MaplibreGl | null }) {
  const { state } = useSessionState();
  const { hoverId, activeId: clientActiveId } = useHover();
  const lastFeatureStateIds = useRef<Set<string>>(new Set());
  const lastPannedId = useRef<string | null>(null);

  // Selected listing for the map. Prefer the client-click selection (hover
  // store — reliably reaches this component), fall back to the agent-driven
  // selection in SessionState (open_listing, delivered over SSE).
  const activeId = clientActiveId ?? state?.active_id ?? null;

  const geojson = useMemo<FeatureCollection<Point, ApartmentProps>>(() => {
    const features = decodeMarkers(state?.result_markers).map((m) => ({
      type: "Feature" as const,
      id: m.id,
      geometry: { type: "Point" as const, coordinates: [m.lng, m.lat] },
      properties: {
        id: m.id,
        price_warm_eur: m.price_warm_eur,
      },
    }));
    return { type: "FeatureCollection", features };
  }, [state?.result_markers]);

  // id → [lng, lat] for the active result set, so a card/pin click can pan
  // the map to the selected listing.
  const coordsById = useMemo(() => {
    const m = new Map<string, [number, number]>();
    for (const mk of decodeMarkers(state?.result_markers)) {
      m.set(mk.id, [mk.lng, mk.lat]);
    }
    return m;
  }, [state?.result_markers]);

  // Pan (and gently zoom in only when far out) to the active listing whenever
  // the selection changes. Highlight is handled by the feature-state effect.
  useEffect(() => {
    if (!activeId) {
      lastPannedId.current = null; // allow re-pan if the same id is re-selected
      return;
    }
    if (activeId === lastPannedId.current) return;
    const m = map;
    if (!m) return;
    let center = coordsById.get(activeId);
    if (!center) {
      const d = state?.active_listing_detail;
      if (d && d.latitude != null && d.longitude != null) {
        center = [d.longitude, d.latitude];
      }
    }
    if (!center) return;
    lastPannedId.current = activeId;
    const opts: { center: [number, number]; duration: number; zoom?: number } = {
      center,
      duration: 500,
    };
    if (m.getZoom() < 12.5) opts.zoom = 14; // gentle zoom-in only when zoomed far out
    m.easeTo(opts);
  }, [map, activeId, state?.active_listing_detail, coordsById]);

  // Drive hover + active visual state by setFeatureState. Track which ids
  // we touched last frame so we can clean them up — feature-state persists
  // until explicitly reset.
  useEffect(() => {
    const m = map;
    if (!m) return;
    const src = "apartments";
    const next = new Set<string>();
    if (hoverId) next.add(hoverId);
    if (activeId) next.add(activeId);
    for (const id of lastFeatureStateIds.current) {
      if (!next.has(id)) {
        m.removeFeatureState({ source: src, id });
      }
    }
    if (hoverId) m.setFeatureState({ source: src, id: hoverId }, { hover: true });
    if (activeId) {
      m.setFeatureState({ source: src, id: activeId }, { active: true });
    }
    lastFeatureStateIds.current = next;
  }, [map, hoverId, activeId, state?.result_markers]);

  return (
    <Source
      id="apartments"
      type="geojson"
      data={geojson}
      cluster
      clusterRadius={50}
      clusterMaxZoom={14}
      promoteId="id"
    >
      <Layer {...CLUSTER_LAYER} />
      <Layer {...CLUSTER_COUNT_LAYER} />
      <Layer {...PIN_HALO_LAYER} />
      <Layer {...PIN_LAYER} />
    </Source>
  );
}

interface ApartmentProps {
  id: string;
  price_warm_eur: number | null;
}
