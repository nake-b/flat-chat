// Manual TypeScript mirror of the backend Pydantic SessionState
// (services/backend/src/flat_chat/chat/session_state.py).
// Keep these two files in sync — fields and optionality must match exactly.
//
// `result_markers` mirrors the SERIALIZED columnar wire shape that crosses
// AG-UI (parallel arrays), NOT the backend's in-memory `list[Marker]` —
// CopilotKit stores whatever the backend dumps verbatim, and the backend
// dumps the columnar form. Use `decodeMarkers()` to zip it back into objects.
//
// Per-listing context shapes mirror services/backend/src/flat_chat/listings/context.py.
// Label literal vocab traces to agent-compound-docs/decisions/geo-context-thresholds.md.
//
// Architecture-decision docs:
//   - agent-compound-docs/decisions/session-state-design.md (state shape + naming)
//   - agent-compound-docs/decisions/agent-vs-http-data-flow.md (the 3-tier model)

// ---------------------------------------------------------------------------
// Geo-context labels (literal unions matching Pydantic Literals)
// ---------------------------------------------------------------------------

export type NoiseLabel = "quiet" | "lively" | "noisy";
export type DensityLabel = "sparse" | "moderate" | "dense";
export type GreeneryLabel = "concrete" | "leafy" | "very_leafy";
export type GtfsMode =
  | "mainline"
  | "regional"
  | "s_bahn"
  | "u_bahn"
  | "bus"
  | "tram"
  | "ferry";
export type HospitalTier = "plan_hospital" | "other";

// ---------------------------------------------------------------------------
// Per-listing detail shapes (mirror of listings/context.py)
// ---------------------------------------------------------------------------

export interface NearestTransitStop {
  stop_id: string;
  name: string;
  modes: GtfsMode[];
  lines: string[];
  distance_m: number;
  walk_minutes: number | null;
}

export interface NearestSchool {
  name: string | null;
  school_type: string | null;
  distance_m: number;
}

export interface SchoolCatchmentInfo {
  catchment_id: string | null;
  school_number: string | null;
  school_name: string | null;
}

export interface NearestPark {
  name: string | null;
  distance_m: number;
}

export interface NearestPlayground {
  name: string | null;
  distance_m: number;
}

export interface NearestHospital {
  name: string | null;
  tier: HospitalTier | null;
  distance_m: number;
}

export interface NearestWater {
  name: string | null;
  water_kind: string | null;
  distance_m: number;
}

export interface NearestKita {
  name: string | null;
  distance_m: number;
}

export interface NearestLandmark {
  name: string | null;
  category: string | null;
  distance_m: number;
}

export interface NoiseProfile {
  label: NoiseLabel | null;
  total_lden: number | null;
  total_lnight: number | null;
  street_lden: number | null;
  rail_lden: number | null;
  distance_m: number | null;
}

export interface GreeneryProfile {
  label: GreeneryLabel | null;
  green_m2_within_300m: number | null;
}

export interface DensityProfile {
  label: DensityLabel | null;
  persons_per_hectare: number | null;
  population: number | null;
  age_under_6: number | null;
  age_6_to_10: number | null;
  age_10_to_18: number | null;
  age_18_to_65: number | null;
  age_65_to_70: number | null;
  age_70_to_75: number | null;
  age_75_to_80: number | null;
  age_80_plus: number | null;
}

// Tier-3 detail blob — fetched via GET /api/listings/{id} (primary) or
// pushed by the agent's `open_listing` tool via state delta.
export interface ListingDetail {
  id: string;
  title: string | null;
  description: string | null;
  address: string | null;
  district: string | null;
  postal_code: string | null;
  latitude: number | null;
  longitude: number | null;

  // Admin-area context (ALKIS polygon assignment + Umweltzone ring flag)
  inside_ring: boolean | null;
  listing_bezirk: string | null;
  listing_ortsteil: string | null;

  price_warm_eur: number | null;
  price_cold_eur: number | null;
  nebenkosten_eur: number | null;
  kaution_eur: number | null;

  rooms: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  area_sqm: number | null;

  floor: number | null;
  floors_total: number | null;
  construction_year: number | null;
  available_from: string | null;
  listing_type: string | null;

  heating: string | null;
  energy_consumption_kwh: number | null;

  wbs_required: boolean | null;
  is_furnished: boolean | null;
  has_kitchen: boolean | null;
  has_balcony: boolean | null;
  has_elevator: boolean | null;
  has_garden: boolean | null;

  features: unknown[] | null;
  images: string[];

  lister_type: string | null;
  source_url: string | null;

  // Geo-context tier-3
  nearest_transit_stops: NearestTransitStop[];
  school_catchment: SchoolCatchmentInfo | null;
  nearest_schools: NearestSchool[];
  nearest_parks: NearestPark[];
  nearest_playground: NearestPlayground | null;
  nearest_hospitals: NearestHospital[];
  nearest_water: NearestWater | null;
  nearest_kitas: NearestKita[];
  nearest_landmarks: NearestLandmark[];
  noise: NoiseProfile | null;
  greenery: GreeneryProfile | null;
  density: DensityProfile | null;
  disabled_parking_count: number;
}

// ---------------------------------------------------------------------------
// ListingCard — tier-2 card shape. ~500 bytes per listing; the frontend
// renders markers + cards from a list of these.
// ---------------------------------------------------------------------------

export interface ListingCard {
  id: string;
  lat: number | null;
  lng: number | null;
  // Money
  price_warm_eur: number | null;
  price_cold_eur: number | null;
  nebenkosten_eur: number | null;
  kaution_eur: number | null;
  // Size
  rooms: number | null;
  bedrooms: number | null;
  area_sqm: number | null;
  // Building / availability
  floor: number | null;
  floors_total: number | null;
  available_from: string | null;
  listing_type: string | null;
  // Location
  district: string | null;
  title: string | null;
  address: string | null;
  // Amenities (most-asked subset surfaced as chips)
  wbs_required: boolean | null;
  is_furnished: boolean | null;
  has_balcony: boolean | null;
  has_kitchen: boolean | null;
  has_elevator: boolean | null;
  has_garden: boolean | null;
  // Energy
  heating: string | null;
  energy_consumption_kwh: number | null;
  // Listing source signal
  lister_type: string | null;
  // Outbound link + first image (gallery is in ListingDetail.images)
  source_url: string | null;
  image_url: string | null;
  // Geo-context chips — derived from gold via listings/labels in backend
  nearest_transit_line: string | null;
  walk_min_to_transit: number | null;
  nearest_park_name: string | null;
  nearest_park_m: number | null;
  noise_label: NoiseLabel | null;
  density_label: DensityLabel | null;
  // Admin-area context — cheap scalars for location chips
  inside_ring: boolean | null;
  listing_bezirk: string | null;
  listing_ortsteil: string | null;
  // Semantic-search score
  similarity_score: number | null;
}

// ---------------------------------------------------------------------------
// SearchParams — applied filters, mirrored from the backend SessionState so
// the frontend can render "filters in effect" chips (future filter UI).
// Loosely typed here since the frontend doesn't dispatch search itself;
// it only displays what the agent applied.
// ---------------------------------------------------------------------------

export type SearchParams = Record<string, unknown>;

// ---------------------------------------------------------------------------
// ResultMarkers — the COLUMNAR wire shape for the full result set (≤5000
// matches). Parallel, index-aligned arrays: the i-th match is
// { id: ids[i], lat: lats[i], lng: lngs[i], channel_value: values[i] }.
// `values` is the single active visualization scalar (warm rent by default,
// commute minutes under a travel lens — see `MarkerChannel`). This is what
// CopilotKit stores verbatim — both the map source and the ordered result
// set. Decode into objects with `decodeMarkers()`.
// ---------------------------------------------------------------------------

export interface ResultMarkers {
  ids: string[];
  lats: number[];
  lngs: number[];
  values: (number | null)[];
  // Legacy column name for snapshots persisted before the channel
  // generalization; `decodeMarkers` falls back to it. New backends emit
  // `values`.
  prices?: (number | null)[];
}

// A single decoded marker — one row zipped out of the parallel arrays.
// `channel_value` is whatever `SessionState.marker_channel` currently names.
export interface MarkerPoint {
  id: string;
  lat: number;
  lng: number;
  channel_value: number | null;
}

// Zip the parallel arrays into objects. Guards null/undefined → []. Length
// is driven by `ids`; positional fields fall back to null when arrays are
// ragged (defensive against partial state deltas).
export function decodeMarkers(
  m: ResultMarkers | null | undefined,
): MarkerPoint[] {
  if (!m || !m.ids) return [];
  const col = m.values ?? m.prices; // back-compat with the legacy key
  const out: MarkerPoint[] = [];
  for (let i = 0; i < m.ids.length; i++) {
    const lat = m.lats?.[i];
    const lng = m.lngs?.[i];
    // Skip ragged rows missing coordinates — a marker without lat/lng is
    // meaningless to both the map and the card list.
    if (lat == null || lng == null) continue;
    out.push({
      id: m.ids[i],
      lat,
      lng,
      channel_value: col?.[i] ?? null,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// MarkerChannel — names the single scalar every marker's `channel_value`
// carries (the active map visualization channel). Mirror of
// listings/context.py:MarkerChannel. Backend sets SEMANTICS (`key` + `label`);
// APPEARANCE (colour ramp / domain / number format) is decided here in
// `state/channelStyles.ts`, keyed off `key`. Default `price_warm` → the plain
// pin (no heatmap); `commute_min` → a travel-time ramp.
// ---------------------------------------------------------------------------

export interface MarkerChannel {
  key: string;
  label: string | null;
}

// The active commute lens, if any. Mirror of context.py:TravelTimeFilter.
// Drives the channel label + (when max_minutes is set) the dropped markers.
export interface TravelTimeFilter {
  anchor_label: string;
  anchor_lat: number;
  anchor_lng: number;
  mode: "transit" | "car";
  max_minutes: number | null;
}

// ---------------------------------------------------------------------------
// Map overlays — geometries the agent draws on the map (the Spree, a U-Bahn
// line, a Bezirk, the inside-the-ring zone). Mirror of
// listings/context.py:MapOverlay. The backend sets SEMANTICS only
// (kind/label/geojson/origin); APPEARANCE is decided here in
// `state/overlayStyles.ts`, keyed off `kind` + the geojson geometry type.
// `geojson` is a raw GeoJSON geometry (from PostGIS ST_AsGeoJSON).
// ---------------------------------------------------------------------------

export type OverlayKind =
  | "place"
  | "transit_line"
  | "bezirk"
  | "ring"
  | "parks";
export type OverlayOrigin = "search" | "pinned";

// A labelled point decorating an overlay — currently a transit line's served
// stations (rendered as dots + line badges). Mirror of context.py:OverlayPoint.
export interface OverlayPoint {
  label: string;
  lon: number;
  lat: number;
}

export interface MapOverlay {
  id: string;
  kind: OverlayKind;
  label: string;
  // GeoJSON geometry object ({type, coordinates}); typed loosely so a Feature
  // would also pass. The map layer reads `.type` to pick line vs fill.
  geojson: GeoJSON.Geometry;
  origin: OverlayOrigin;
  // Optional decorations on the geometry — a transit line's stations; empty/
  // absent for everything else. Optional so older state snapshots still parse.
  points?: OverlayPoint[];
}

// ---------------------------------------------------------------------------
// SessionState — the canonical in-memory representation mirrored from
// backend over the AG-UI stream. The frontend renders markers + cards from
// this; the LLM reads the same fields via build_dynamic_state_prompt.
//
// Tiered result set: `result_markers` carries EVERY match (≤5000) as the
// columnar map source + ordered list; `preview_cards` carries the top-10
// full cards hot for instant first paint. The rest of the cards are
// hydrated lazily via GET /api/listings?ids=…&view=card as the user scrolls.
// ---------------------------------------------------------------------------

export interface SessionState {
  search_params: SearchParams | null;
  total_results: number;
  result_markers: ResultMarkers;
  preview_cards: ListingCard[];
  active_id: string | null;
  active_listing_detail: ListingDetail | null;
  map_overlays: MapOverlay[];
  marker_channel: MarkerChannel;
  travel_time_filter: TravelTimeFilter | null;
}

export const EMPTY_SESSION_STATE: SessionState = Object.freeze({
  search_params: null,
  total_results: 0,
  result_markers: { ids: [], lats: [], lngs: [], values: [] },
  preview_cards: [],
  active_id: null,
  active_listing_detail: null,
  map_overlays: [],
  marker_channel: { key: "price_warm", label: null },
  travel_time_filter: null,
}) as SessionState;

export const AGENT_NAME = "berlin-agent";

// ---------------------------------------------------------------------------
// Backwards-compat alias — let existing imports of `UiState` keep working
// while the rest of the frontend migrates over. Will go away once every
// component switches to `useSessionState` / `SessionState` naming.
// ---------------------------------------------------------------------------

export type UiState = SessionState;
export const EMPTY_UI_STATE = EMPTY_SESSION_STATE;
