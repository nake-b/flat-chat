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

// The apartment pin — a single clean teardrop, recoloured by state. No glow
// layer (it read as a muddy blob); selection is conveyed by colour: default
// grey, hover red, selected the brand red. A crisp white halo outlines every
// pin for legibility against the map, thicker on the selected one. Same layer
// id ("unclustered-point") as the old circle so click/hover wiring +
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
      ["boolean", ["feature-state", "active"], false],
      RED,
      ["boolean", ["feature-state", "hover"], false],
      RED,
      GREY,
    ],
    "icon-halo-color": "#ffffff",
    "icon-halo-width": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      2.5,
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
      duration: 850,
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
      <Layer {...PIN_LAYER} />
    </Source>
  );
}

interface ApartmentProps {
  id: string;
  price_warm_eur: number | null;
}
