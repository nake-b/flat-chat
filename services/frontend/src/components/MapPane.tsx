import { useCallback, useEffect, useMemo, useRef } from "react";
import { Map as MapLibreMap, Source, Layer, useMap } from "@vis.gl/react-maplibre";
import type {
  CircleLayerSpecification,
  GeoJSONSource,
  MapLayerMouseEvent,
  SymbolLayerSpecification,
} from "maplibre-gl";
import type { FeatureCollection, Point } from "geojson";

import { useUiState } from "../hooks/useUiState";
import { useHover } from "../hooks/useHover";
import { EMPTY_UI_STATE, type UiApartment } from "../state/UiState";

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

// Translucent halo behind a selected dot — drawn first so the active circle
// sits on top of it. Only visible when feature-state.active is true.
const POINT_HALO_LAYER = {
  id: "unclustered-point-halo",
  type: "circle",
  source: "apartments",
  filter: ["!", ["has", "point_count"]],
  paint: {
    "circle-color": RED,
    "circle-opacity": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      0.22,
      0,
    ],
    "circle-radius": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      18,
      0,
    ],
    "circle-radius-transition": { duration: 220, delay: 0 },
    "circle-opacity-transition": { duration: 220, delay: 0 },
    "circle-stroke-width": 0,
  },
} as unknown as CircleLayerSpecification;

const POINT_LAYER = {
  id: "unclustered-point",
  type: "circle",
  source: "apartments",
  filter: ["!", ["has", "point_count"]],
  paint: {
    "circle-color": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      INK,
      ["boolean", ["feature-state", "hover"], false],
      RED,
      "#3A3A3A",
    ],
    "circle-radius": [
      "case",
      ["boolean", ["feature-state", "active"], false],
      9,
      ["boolean", ["feature-state", "hover"], false],
      8,
      5,
    ],
    "circle-radius-transition": { duration: 180, delay: 0 },
    "circle-color-transition": { duration: 140, delay: 0 },
    "circle-stroke-color": "#ffffff",
    "circle-stroke-width": 2,
  },
} as unknown as CircleLayerSpecification;

export function MapPane() {
  // Click + hover handlers live at the MapLibreMap level so we use
  // react-maplibre's React event dispatch path — far more reliable than
  // raw `m.on('click', ...)` listeners attached via useEffect, which
  // suffer from closure / re-attach races against CopilotKit's frequent
  // state updates. `interactiveLayerIds` populates `e.features` with the
  // hits at the click/move point, so we don't need queryRenderedFeatures.
  const { setState } = useUiState();
  const { setHover } = useHover();

  // Stable refs so the handler closures always see the LATEST setState /
  // setHover without needing to re-bind on every render. React's useState
  // setters are stable; useCoAgent's may not be.
  const setStateRef = useRef(setState);
  setStateRef.current = setState;
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

    // Unclustered apartment dot — open detail in cards pane.
    const id = f.properties?.id ?? (f.id as string | undefined);
    if (id) {
      const next = String(id);
      setStateRef.current((prev) => ({ ...(prev ?? EMPTY_UI_STATE), active_id: next }));
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
      }}
    >
      <ApartmentLayer />
    </MapLibreMap>
  );
}

function ApartmentLayer() {
  const { state } = useUiState();
  const { hoverId } = useHover();
  const { "apartments-map": map } = useMap();
  const lastFeatureStateIds = useRef<Set<string>>(new Set());

  const geojson = useMemo<FeatureCollection<Point, ApartmentProps>>(() => {
    const features = (state?.results ?? [])
      .filter((a): a is ApartmentWithCoords => a.lat != null && a.lng != null)
      .map((a) => ({
        type: "Feature" as const,
        id: a.id,
        geometry: { type: "Point" as const, coordinates: [a.lng, a.lat] },
        properties: {
          id: a.id,
          price_warm_eur: a.price_warm_eur,
          title: a.title,
          district: a.district,
        },
      }));
    return { type: "FeatureCollection", features };
  }, [state?.results]);

  // Drive hover + active visual state by setFeatureState. Track which ids
  // we touched last frame so we can clean them up — feature-state persists
  // until explicitly reset.
  useEffect(() => {
    const m = map?.getMap();
    if (!m) return;
    const src = "apartments";
    const next = new Set<string>();
    if (hoverId) next.add(hoverId);
    if (state?.active_id) next.add(state.active_id);
    for (const id of lastFeatureStateIds.current) {
      if (!next.has(id)) {
        m.removeFeatureState({ source: src, id });
      }
    }
    if (hoverId) m.setFeatureState({ source: src, id: hoverId }, { hover: true });
    if (state?.active_id) {
      m.setFeatureState({ source: src, id: state.active_id }, { active: true });
    }
    lastFeatureStateIds.current = next;
  }, [map, hoverId, state?.active_id, state?.results]);

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
      <Layer {...POINT_HALO_LAYER} />
      <Layer {...POINT_LAYER} />
    </Source>
  );
}

interface ApartmentProps {
  id: string;
  price_warm_eur: number | null;
  title: string | null;
  district: string | null;
}

type ApartmentWithCoords = UiApartment & { lat: number; lng: number };
