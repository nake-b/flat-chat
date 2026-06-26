import { create } from "zustand";

// Hover state is pure rendering ephemera — the agent doesn't need to reason
// about it, so it lives client-local rather than in UiState. Both the map and
// the card strip read & write the same `hoverId` to drive bidirectional
// highlight (hover a card → highlight its marker, and vice versa).
//
// `activeId` is the client-side mirror of the selected listing for the MAP.
// We need it because a card click updates `SessionState.active_id` via
// CopilotKit's `useCoAgent` setState, and that client-side write does NOT
// reliably re-render every `useCoAgent` consumer (the map's ApartmentLayer in
// particular stays stale, so its pan + highlight effects never fire). Agent-
// pushed state (markers, agent-driven open_listing) DOES reach the map over
// SSE — so the map reads `activeId ?? state.active_id` to cover both the
// client-click path (this store) and the agent path (SessionState).

interface HoverStore {
  hoverId: string | null;
  activeId: string | null;
  setHover: (id: string | null) => void;
  setActive: (id: string | null) => void;
  reset: () => void;
}

export const useHover = create<HoverStore>((set) => ({
  hoverId: null,
  activeId: null,
  setHover: (id) => set({ hoverId: id }),
  setActive: (id) => set({ activeId: id }),
  reset: () => set({ hoverId: null, activeId: null }),
}));
