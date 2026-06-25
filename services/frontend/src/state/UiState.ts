// Compatibility re-export. The canonical home for the shared state
// mirror is `./SessionState.ts` (renamed per the layering refactor —
// see agent-compound-docs/decisions/session-state-design.md). New
// imports should go directly through `./SessionState`; this file
// exists so existing components don't all need rewriting in one pass.

export {
  AGENT_NAME,
  EMPTY_SESSION_STATE,
  EMPTY_UI_STATE,
} from "./SessionState";
export type {
  DensityLabel,
  DensityProfile,
  GreeneryLabel,
  GreeneryProfile,
  GtfsMode,
  HospitalTier,
  ListingDetail,
  NearestHospital,
  NearestKita,
  NearestPark,
  NearestPlayground,
  NearestSchool,
  NearestTransitStop,
  NearestWater,
  NoiseLabel,
  NoiseProfile,
  SchoolCatchmentInfo,
  SearchParams,
  SessionState,
  UiApartment,
  UiState,
} from "./SessionState";

// `ListingContext` was the old name for the per-listing detail blob — it
// now lives inside `ListingDetail` (carrying both the listing's own
// fields and the geo-context tier-3 blob in one shape). The compat
// alias keeps existing `CardDetail.tsx`-style consumers compiling; new
// code should reference `ListingDetail` directly.
export type { ListingDetail as ListingContext } from "./SessionState";
