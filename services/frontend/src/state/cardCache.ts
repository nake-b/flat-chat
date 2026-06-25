import { create } from "zustand";

import { type ListingCard } from "./SessionState";

// Client-owned hydrated-card cache — the tier-2 cards we've fetched (or
// been handed via `preview_cards`) keyed by listing id. NOT part of
// SessionState: pure client view-state the agent never reasons about.
//
// Mirrors the zustand pattern in hooks/useHover.ts. Shared between:
//   - CardStrip (writes): seeds from `preview_cards`, fills from batch
//     `GET /api/listings?ids=…&view=card` fetches as the user scrolls.
//   - CardsPane (reads): resolves the active tier-2 card by id to hand to
//     CardDetail.
//
// `cards` is a Map so upserts are O(1) and lookups by id are trivial. We
// replace the Map reference on every mutation so zustand's referential-
// equality check fires and subscribers re-render.

interface CardCacheStore {
  cards: Map<string, ListingCard>;
  // Upsert a batch of cards (by id). Existing entries are overwritten.
  merge: (incoming: ListingCard[]) => void;
  clear: () => void;
}

export const useCardCache = create<CardCacheStore>((set) => ({
  cards: new Map(),
  merge: (incoming) =>
    set((state) => {
      if (incoming.length === 0) return state;
      const next = new Map(state.cards);
      for (const card of incoming) next.set(card.id, card);
      return { cards: next };
    }),
  clear: () => set({ cards: new Map() }),
}));
