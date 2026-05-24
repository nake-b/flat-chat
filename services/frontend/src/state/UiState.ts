// Manual TypeScript mirror of the backend Pydantic UiState
// (services/backend/src/flat_chat/chat/ui_state.py).
// Keep these two files in sync — fields and optionality must match exactly.

export interface UiApartment {
  id: string;
  lat: number | null;
  lng: number | null;
  price_warm_eur: number | null;
  rooms: number | null;
  area_sqm: number | null;
  district: string | null;
  title: string | null;
  address: string | null;
  source_url: string | null;
  image_url: string | null;
}

export interface UiState {
  results: UiApartment[];
  active_id: string | null;
  tool_logs: string[];
}

export const EMPTY_UI_STATE: UiState = {
  results: [],
  active_id: null,
  tool_logs: [],
};

export const AGENT_NAME = "berlin-agent";
