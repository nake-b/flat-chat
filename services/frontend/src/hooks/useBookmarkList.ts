import { useEffect } from "react";

import type { ListingCard } from "../state/SessionState";
import { useBookmarks } from "../state/useBookmarks";
import { useBookmarkCards, type ListStatus } from "../state/useBookmarkCards";
import { useBookmarkSidebarOpen } from "./useBookmarkSidebarOpen";

// Re-export so existing importers (BookmarkSidebar) keep their import path.
export type { ListStatus };

interface UseBookmarkList {
  items: ListingCard[];
  status: ListStatus;
  refetch: () => void;
}

// Tier-2 cards for the bookmark sidebar (and the bookmark-mode map markers).
// Thin wrapper over the shared `useBookmarkCards` store: multiple mounts (App +
// MapPane) read one list and dedupe to a single fetch instead of each holding a
// divergent copy. Fetches when the sidebar opens AND when the bookmark id set
// changes (a star toggle elsewhere → the row list reconciles on the next
// refetch). Concurrent refetches from the two consumers collapse in the store
// (a later call aborts the earlier).
export function useBookmarkList(): UseBookmarkList {
  const items = useBookmarkCards((s) => s.items);
  const status = useBookmarkCards((s) => s.status);
  const refetch = useBookmarkCards((s) => s.refetch);

  const open = useBookmarkSidebarOpen((s) => s.open);
  // Subscribe to the SIZE only, not the Set instance — toggle() always creates
  // a new Set, but we only need to know the count changed.
  const idsCount = useBookmarks((s) => s.ids.size);

  // Fetch on sidebar-open and on bookmark-set change. We DON'T clear items when
  // the sidebar closes — keeping them means re-opening repaints instantly from
  // cache while the next refetch reconciles.
  useEffect(() => {
    if (open) refetch();
  }, [open, idsCount, refetch]);

  return { items, status, refetch };
}
