import { useCallback, useEffect, useRef, useState } from "react";

import { listBookmarks } from "../api/bookmarks";
import type { ListingCard } from "../state/SessionState";
import { useBookmarks } from "../state/useBookmarks";
import { useBookmarkSidebarOpen } from "./useBookmarkSidebarOpen";

export type ListStatus = "idle" | "loading" | "ready" | "error";

interface UseBookmarkList {
  items: ListingCard[];
  status: ListStatus;
  refetch: () => void;
}

// Tier-2 cards for the bookmark sidebar (and the bookmark-mode map markers).
// Plain useState + useEffect + AbortController — same shape as
// useConversationList. Fetches when the sidebar opens AND when the bookmark
// id set changes (a star toggle elsewhere → the row list reconciles within
// the next refetch).
export function useBookmarkList(): UseBookmarkList {
  const [items, setItems] = useState<ListingCard[]>([]);
  const [status, setStatus] = useState<ListStatus>("idle");
  const inFlightRef = useRef<AbortController | null>(null);

  const open = useBookmarkSidebarOpen((s) => s.open);
  // Subscribe to the SIZE only, not the Set instance — toggle() always
  // creates a new Set, but we only need to know the count changed.
  const idsCount = useBookmarks((s) => s.ids.size);

  const refetch = useCallback(() => {
    inFlightRef.current?.abort();
    const ctrl = new AbortController();
    inFlightRef.current = ctrl;
    setStatus((prev) => (prev === "ready" ? "ready" : "loading"));
    void (async () => {
      try {
        const rows = await listBookmarks(ctrl.signal);
        if (ctrl.signal.aborted) return;
        setItems(rows);
        setStatus("ready");
      } catch (err) {
        if (ctrl.signal.aborted) return;
        if ((err as DOMException)?.name === "AbortError") return;
        setStatus("error");
      }
    })();
  }, []);

  // Fetch on sidebar-open and on bookmark-set change. We DON'T clear items
  // when the sidebar closes — keeping them in state means re-opening repaints
  // instantly from cache while the next refetch reconciles.
  useEffect(() => {
    if (open) refetch();
  }, [open, idsCount, refetch]);

  useEffect(
    () => () => {
      inFlightRef.current?.abort();
    },
    [],
  );

  return { items, status, refetch };
}
