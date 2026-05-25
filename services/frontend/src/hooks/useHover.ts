import { create } from "zustand";

// Hover state is pure rendering ephemera — the agent doesn't need to reason
// about it, so it lives client-local rather than in UiState. Both the map and
// the card strip read & write the same `hoverId` to drive bidirectional
// highlight (hover a card → highlight its marker, and vice versa).

interface HoverStore {
  hoverId: string | null;
  setHover: (id: string | null) => void;
}

export const useHover = create<HoverStore>((set) => ({
  hoverId: null,
  setHover: (id) => set({ hoverId: id }),
}));
