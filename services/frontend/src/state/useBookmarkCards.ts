import { create } from "zustand";

import { listBookmarks } from "../api/bookmarks";
import type { ListingCard } from "./SessionState";

export type ListStatus = "idle" | "loading" | "ready" | "error";

// Shared source of truth for the hydrated tier-2 bookmark cards. Lives in a
// zustand store (not per-hook useState) so BOTH consumers — the bookmark
// sidebar (via App) and the bookmark-mode map markers (via MapPane) — read one
// list and trigger one fetch instead of each holding a divergent copy.
//
// The in-flight controller is module-level so concurrent `refetch()` calls
// (e.g. both consumers mounting on sidebar-open) collapse to a single settling
// request — a later call aborts the earlier one. Items are kept when the
// sidebar closes so a re-open repaints instantly from cache while the next
// refetch reconciles. Same fetch semantics as the old useBookmarkList; only the
// storage moved from local state to a shared store.
interface BookmarkCardsStore {
  items: ListingCard[];
  status: ListStatus;
  refetch: () => void;
}

let inFlight: AbortController | null = null;

export const useBookmarkCards = create<BookmarkCardsStore>((set, get) => ({
  items: [],
  status: "idle",

  refetch: () => {
    inFlight?.abort();
    const ctrl = new AbortController();
    inFlight = ctrl;
    // Skip the skeleton flash once we've loaded at least once.
    set({ status: get().status === "ready" ? "ready" : "loading" });
    void (async () => {
      try {
        const rows = await listBookmarks(ctrl.signal);
        if (ctrl.signal.aborted) return;
        set({ items: rows, status: "ready" });
      } catch (err) {
        if (ctrl.signal.aborted) return;
        if ((err as DOMException)?.name === "AbortError") return;
        set({ status: "error" });
      }
    })();
  },
}));
