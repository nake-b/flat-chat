// Manual TypeScript mirror of the backend Pydantic UiState
// (services/backend/src/flat_chat/chat/ui_state.py).
// Keep these two files in sync — fields and optionality must match exactly.

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
}

export interface UiState {
  results: UiApartment[];
  active_id: string | null;
}

export const EMPTY_UI_STATE: UiState = Object.freeze({
  results: [],
  active_id: null,
}) as UiState;

export const AGENT_NAME = "berlin-agent";
