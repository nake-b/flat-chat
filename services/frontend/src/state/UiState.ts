// Manual TypeScript mirror of the backend Pydantic UiState
// (services/backend/src/flat_chat/chat/ui_state.py).
// Keep these two files in sync — fields and optionality must match exactly.
//
// Geo-context shapes mirror services/backend/src/flat_chat/search/geo_filters.py.
// Label literal vocab traces to agent-compound-docs/decisions/geo-context-thresholds.md.

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
// Per-listing context shapes (mirror of geo_filters.py)
// ---------------------------------------------------------------------------

export interface NearestTransitStop {
  stop_id: string;
  name: string;
  modes: GtfsMode[];
  lines: string[];
  distance_m: number;
  walk_minutes: number;
}

export interface NearestSchool {
  name: string | null;
  school_type: string | null;
  distance_m: number;
  operator: string | null;
}

export interface SchoolCatchmentInfo {
  catchment_id: string | null;
  school_number: string | null;
  school_name: string | null;
}

export interface NearestPark {
  name: string | null;
  object_type: string | null;
  distance_m: number;
  area_m2: number | null;
}

export interface NearestPlayground {
  name: string | null;
  distance_m: number;
  play_area_m2: number | null;
}

export interface NearestHospital {
  name: string | null;
  tier: HospitalTier;
  distance_m: number;
  total_beds: number | null;
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
}

export interface GreeneryProfile {
  label: GreeneryLabel | null;
  green_m2_within_300m: number | null;
}

export interface DensityProfile {
  label: DensityLabel | null;
  persons_per_hectare: number | null;
  age_under_18_pct: number | null;
  age_65_plus_pct: number | null;
}

export interface MssProfile {
  status_label: MssStatus | null;
  dynamics_label: MssDynamics | null;
  social_inequality_label: string | null;
  residents: number | null;
}

export interface ListingContext {
  transit: NearestTransitStop[];
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
// UiApartment (with geo-context chip fields)
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
  // Outbound link + (deferred) image
  source_url: string | null;
  image_url: string | null;
  // Geo-context chips — populated by GeoContextService.apply_chips() via
  // LATERAL joins. All-English labels; German source labels never leak past
  // the backend boundary.
  nearest_transit_line: string | null;
  walk_min_to_transit: number | null;
  nearest_park_name: string | null;
  nearest_park_m: number | null;
  noise_label: NoiseLabel | null;
  density_label: DensityLabel | null;
  mss_status_label: MssStatus | null;
  mss_dynamics_label: MssDynamics | null;
}

export interface UiState {
  results: UiApartment[];
  active_id: string | null;
  // Full geo-context blob for the currently expanded listing — populated when
  // the agent calls get_listing_details(id). Cleared on next search.
  active_listing_context: ListingContext | null;
}

export const EMPTY_UI_STATE: UiState = Object.freeze({
  results: [],
  active_id: null,
  active_listing_context: null,
}) as UiState;

export const AGENT_NAME = "berlin-agent";
