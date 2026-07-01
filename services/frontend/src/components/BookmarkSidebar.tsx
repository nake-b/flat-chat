import { useEffect, useMemo, useState } from "react";

import type { ListingCard } from "../state/SessionState";
import type { ListStatus } from "../hooks/useBookmarkList";
import { BookmarkSidebarItem } from "./BookmarkSidebarItem";
import { ConfirmDialog } from "./ConfirmDialog";

interface Props {
  open: boolean;
  items: ListingCard[];
  status: ListStatus;
  activeId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

// Bookmark panel. Rendered INSIDE the chat column's <aside> (App.tsx), so it
// overlays only the chat — `absolute inset-0` fills the column, opaque
// `bg-paper` fully hides the chat beneath, and the slide transform animates it
// in/out. No backdrop (nothing else on screen is dimmed); the map column stays
// visible and interactive so "Go to map" pans a map the user can see. Always
// mounted so close ALSO animates; `data-open` drives the transform.
//
// Browsing surface, not a thin list: a search box filters rows by title, and
// each row is a detailed card with a "Go to map" pan button. No confirm dialog
// on remove — it's cheap and reversible (re-click the star).
export function BookmarkSidebar({
  open,
  items,
  status,
  activeId,
  onClose,
  onSelect,
  onRemove,
}: Props) {
  const [query, setQuery] = useState("");
  // `pendingRemoveId` opens the confirm modal and remembers which bookmark is
  // up for removal. The row's star sets it; confirm calls the real `onRemove`.
  const [pendingRemoveId, setPendingRemoveId] = useState<string | null>(null);
  const pendingRow =
    pendingRemoveId !== null
      ? items.find((row) => row.id === pendingRemoveId)
      : undefined;

  useEffect(() => {
    // Escape closes the panel — but not while the confirm modal is open
    // (the dialog owns Escape then, to cancel the remove).
    if (!open || pendingRemoveId !== null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, pendingRemoveId]);

  const confirmRemove = () => {
    if (pendingRemoveId === null) return;
    onRemove(pendingRemoveId);
    setPendingRemoveId(null);
  };

  // Clear transient UI when the panel closes so a re-open starts fresh.
  useEffect(() => {
    if (!open) {
      setQuery("");
      setPendingRemoveId(null);
    }
  }, [open]);

  // Client-side filter over the already-fetched rows. Matches title first
  // (what the user types), falling back to district/address so listings with a
  // null title are still findable by where they are.
  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!q) return items;
    return items.filter((c) => {
      const haystack = [c.title, c.district, c.listing_bezirk, c.address]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [items, q]);

  return (
    <>
    <aside
      id="bookmark-sidebar"
      role="complementary"
      aria-label="Bookmarks"
      aria-hidden={!open}
      data-open={open}
      className={
        "absolute inset-0 z-20 flex flex-col bg-paper " +
        "transition-transform duration-300 ease-snap " +
        "-translate-x-full data-[open=true]:translate-x-0"
      }
    >
      <div className="flex items-center justify-between border-b-2 border-red px-5 pb-3 pt-5">
        <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-soft">
          Bookmarks
          {items.length > 0 ? (
            <span className="ml-1.5 text-ink-ghost">({items.length})</span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close bookmarks"
          className="-mr-1 flex h-7 w-7 items-center justify-center font-mono text-base text-ink-soft transition-colors hover:text-red"
        >
          ×
        </button>
      </div>

      <div className="border-b border-paper-rule px-5 py-3">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search bookmarks…"
          aria-label="Search bookmarks by title"
          className={
            "w-full border border-paper-rule bg-paper-dim px-3 py-2 font-sans " +
            "text-sm text-ink placeholder:text-ink-ghost focus:border-red focus:outline-none"
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {status === "loading" && items.length === 0 ? (
          <SidebarSkeleton />
        ) : status === "error" && items.length === 0 ? (
          <SidebarMessage>Couldn't load bookmarks</SidebarMessage>
        ) : items.length === 0 ? (
          <SidebarMessage>No bookmarks yet</SidebarMessage>
        ) : filtered.length === 0 ? (
          <SidebarMessage>No matches</SidebarMessage>
        ) : (
          <div className="divide-y divide-paper-rule">
            {filtered.map((card) => (
              <BookmarkSidebarItem
                key={card.id}
                card={card}
                active={card.id === activeId}
                onSelect={onSelect}
                onRemove={setPendingRemoveId}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
    <ConfirmDialog
      open={pendingRemoveId !== null}
      title="Remove bookmark?"
      message={
        pendingRow
          ? `"${pendingRow.title ?? "Untitled"}" will be removed from your bookmarks. You can re-add it anytime.`
          : "This listing will be removed from your bookmarks. You can re-add it anytime."
      }
      confirmLabel="Remove"
      cancelLabel="Keep"
      onConfirm={confirmRemove}
      onCancel={() => setPendingRemoveId(null)}
    />
    </>
  );
}

function SidebarSkeleton() {
  return (
    <div className="px-5 py-3" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="my-2 h-24 animate-pulse rounded bg-paper-dim" />
      ))}
    </div>
  );
}

function SidebarMessage({ children }: { children: string }) {
  return (
    <div className="px-5 py-6 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-ghost">
      {children}
    </div>
  );
}
