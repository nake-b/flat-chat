// Manual TypeScript mirror of the backend Pydantic SessionState
// (services/backend/src/flat_chat/chat/session_state.py).
// Keep these two files in sync — fields and optionality must match exactly.
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
export type MssStatus = "disadvantaged" | "lower-income" | "mixed" | "affluent";
export type MssDynamics = "slipping" | "stable" | "improving";
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

export interface NoiseProfile {
  label: NoiseLabel | null;
  total_lden: number | null;
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

export interface MssProfile {
  status: MssStatus | null;
  dynamics: MssDynamics | null;
  social_inequality: string | null;
  planning_area_name: string | null;
  residents: number | null;
  year: number | null;
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
  noise: NoiseProfile | null;
  greenery: GreeneryProfile | null;
  density: DensityProfile | null;
  mss: MssProfile | null;
  disabled_parking_count: number;
}

// ---------------------------------------------------------------------------
// UiApartment — tier-2 card shape. ~500 bytes per listing; the frontend
// renders markers + cards from a list of these.
// ---------------------------------------------------------------------------

export interface UiApartment {
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
  mss_status_label: MssStatus | null;
  mss_dynamics_label: MssDynamics | null;
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
// SessionState — the canonical in-memory representation mirrored from
// backend over the AG-UI stream. The frontend renders markers + cards from
// this; the LLM reads the same fields via build_dynamic_state_prompt.
// ---------------------------------------------------------------------------

export interface SessionState {
  search_params: SearchParams | null;
  total_results: number;
  results: UiApartment[];
  active_id: string | null;
  active_listing_detail: ListingDetail | null;
}

export const EMPTY_SESSION_STATE: SessionState = Object.freeze({
  search_params: null,
  total_results: 0,
  results: [],
  active_id: null,
  active_listing_detail: null,
}) as SessionState;

export const AGENT_NAME = "berlin-agent";

// ---------------------------------------------------------------------------
// Backwards-compat alias — let existing imports of `UiState` keep working
// while the rest of the frontend migrates over. Will go away once every
// component switches to `useSessionState` / `SessionState` naming.
// ---------------------------------------------------------------------------

export type UiState = SessionState;
export const EMPTY_UI_STATE = EMPTY_SESSION_STATE;
