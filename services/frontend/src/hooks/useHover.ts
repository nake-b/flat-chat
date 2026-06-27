import { useEffect } from "react";
import { create } from "zustand";

// Hover state is pure rendering ephemera — the agent doesn't need to reason
// about it, so it lives client-local rather than in UiState. Both the map and
// the card strip read & write the same `hoverId` to drive bidirectional
// highlight (hover a card → highlight its marker, and vice versa).
//
// `activeId` is the client-local MIRROR of the selected listing for the MAP.
// SINGLE SOURCE OF TRUTH = `SessionState.active_id`; this store just caches it
// so the map's `ApartmentLayer` can react to it reliably (a client-side
// `useCoAgent` setState does NOT reliably re-render that consumer, so its pan +
// highlight effects would never fire from a card/pin click alone). The mirror
// is written from BOTH selection paths so there is no client-vs-agent
// precedence to get wrong — only "latest selection wins":
//   - client click → `activate()` writes it synchronously (instant feedback);
//   - agent `open_listing` / reload hydration → arrives as an SSE
//     `state.active_id` delta and `useActiveIdMirror` copies it in.
// Without the mirror for the agent path, a stale client click would mask a
// later agent-driven selection (map stuck on the clicked listing while the
// detail panel followed the agent).

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

// Mirror the authoritative `SessionState.active_id` into the client-local
// store whenever it changes. This covers the selection paths that don't run
// through `activate()` — the agent's `open_listing` and reload hydration, both
// of which arrive as SSE state deltas. Keyed on the id, so it fires only on an
// actual change and never clobbers a fresher synchronous click write. Call it
// once from a component subscribed to SessionState (the map's ApartmentLayer).
export function useActiveIdMirror(stateActiveId: string | null): void {
  useEffect(() => {
    useHover.getState().setActive(stateActiveId);
  }, [stateActiveId]);
}
