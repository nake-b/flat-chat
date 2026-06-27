import { create } from "zustand";

import {
  addBookmark,
  listBookmarkIds,
  removeBookmark,
} from "../api/bookmarks";

// Per-user bookmarked listing ids. Lives OUTSIDE SessionState — SessionState
// mirrors per-conversation backend state, but bookmarks are per-user (they
// persist across new conversations). Single source of truth for "is this
// listing bookmarked?" — the star on every card subscribes here so a toggle
// anywhere flips every star with the same id.
//
// Optimistic: the toggle flips the local set BEFORE the HTTP fires so taps
// feel instant. On HTTP failure we roll back AND refetch to reconcile.
// Hydrated once on app mount (GET /api/bookmarks/ids) so the stars are
// correct on first paint.

interface BookmarksStore {
  ids: Set<string>;
  hydrated: boolean;
  hydrate: () => Promise<void>;
  toggle: (id: string) => Promise<void>;
}

export const useBookmarks = create<BookmarksStore>((set, get) => ({
  ids: new Set(),
  hydrated: false,

  hydrate: async () => {
    try {
      const arr = await listBookmarkIds();
      set({ ids: new Set(arr), hydrated: true });
    } catch (err) {
      // Leave hydrated=false so a later trigger (e.g. opening the sidebar)
      // can retry. A failed hydrate isn't fatal — stars just render as
      // "not bookmarked" until the next successful read.
      console.warn("Bookmark hydrate failed", err);
    }
  },

  toggle: async (id) => {
    const { ids } = get();
    const wasOn = ids.has(id);
    const next = new Set(ids);
    if (wasOn) next.delete(id);
    else next.add(id);
    set({ ids: next });

    try {
      if (wasOn) await removeBookmark(id);
      else await addBookmark(id);
    } catch (err) {
      console.warn("Bookmark toggle failed; rolling back", err);
      const rollback = new Set(get().ids);
      if (wasOn) rollback.add(id);
      else rollback.delete(id);
      set({ ids: rollback });
      // Best-effort reconcile against the server view (could refuse to revive,
      // could disagree with our rollback).
      void get().hydrate();
    }
  },
}));
