import { useEffect } from "react";

import type { ListingCard } from "../state/SessionState";
import type { ListStatus } from "../hooks/useBookmarkList";
import { BookmarkSidebarItem } from "./BookmarkSidebarItem";

interface Props {
  open: boolean;
  items: ListingCard[];
  status: ListStatus;
  activeId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

// Bookmark slide-out. Always mounted (so close ALSO animates); data-open toggle
// drives the transform. Mirrors ConversationSidebar's structure exactly so the
// two sidebars feel identical — same width, same easing, same Esc-to-close
// behaviour. No confirm dialog: removing a bookmark is cheap and reversible
// (re-click the star).
export function BookmarkSidebar({
  open,
  items,
  status,
  activeId,
  onClose,
  onSelect,
  onRemove,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Dismiss bookmarks"
          tabIndex={-1}
          onClick={onClose}
          data-testid="bookmark-sidebar-backdrop"
          className="fixed inset-0 z-40 cursor-default bg-ink/40 transition-opacity duration-200"
        />
      )}
      <aside
        id="bookmark-sidebar"
        role="complementary"
        aria-label="Bookmarks"
        aria-hidden={!open}
        data-open={open}
        className={
          "fixed left-0 top-0 z-50 flex h-screen w-[300px] flex-col border-r " +
          "border-paper-rule bg-paper transition-transform duration-300 ease-snap " +
          "-translate-x-full data-[open=true]:translate-x-0"
        }
      >
        <div className="flex items-center justify-between border-b border-paper-rule px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
            Bookmarks
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close bookmarks"
            className="font-mono text-xs text-ink-soft transition-colors hover:text-red"
          >
            ×
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {status === "loading" && items.length === 0 ? (
            <SidebarSkeleton />
          ) : status === "error" && items.length === 0 ? (
            <SidebarMessage>Couldn't load bookmarks</SidebarMessage>
          ) : items.length === 0 ? (
            <SidebarMessage>No bookmarks yet</SidebarMessage>
          ) : (
            items.map((card) => (
              <BookmarkSidebarItem
                key={card.id}
                card={card}
                active={card.id === activeId}
                onSelect={onSelect}
                onRemove={onRemove}
              />
            ))
          )}
        </div>
      </aside>
    </>
  );
}

function SidebarSkeleton() {
  return (
    <div className="px-4 py-2" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="my-2 h-12 animate-pulse rounded bg-paper-dim" />
      ))}
    </div>
  );
}

function SidebarMessage({ children }: { children: string }) {
  return (
    <div className="px-4 py-6 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-ghost">
      {children}
    </div>
  );
}
