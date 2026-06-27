import { useCallback, useEffect } from "react";

import { deleteConversation } from "./api/conversations";
import { ChatPane } from "./components/ChatPane";
import { MapPane } from "./components/MapPane";
import { CardsPane } from "./components/CardsPane";
import { BookmarkSidebar } from "./components/BookmarkSidebar";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { useBookmarkList } from "./hooks/useBookmarkList";
import { useBookmarkSidebarOpen } from "./hooks/useBookmarkSidebarOpen";
import { useConversationList } from "./hooks/useConversationList";
import { useSessionState } from "./hooks/useSessionState";
import { useSidebarOpen } from "./hooks/useSidebarOpen";
import { useBookmarks } from "./state/useBookmarks";

// Chat-host layout: chat left ~40%, map+cards artifact right ~60%.
// Desktop-only — CLAUDE.md "Out of Scope" lists mobile as deferred.
const TOP_PCT = 70;

function App({
  conversationId,
  onNewConversation,
  onSwitchConversation,
}: {
  conversationId: string;
  onNewConversation: () => void;
  onSwitchConversation: (id: string) => void;
}) {
  const open = useSidebarOpen((s) => s.open);
  const closeSidebar = useSidebarOpen((s) => s.closeSidebar);
  const bookmarkOpen = useBookmarkSidebarOpen((s) => s.open);
  const closeBookmarkSidebar = useBookmarkSidebarOpen((s) => s.closeSidebar);
  const { items, status, refetch, removeOptimistically } = useConversationList();
  const {
    items: bookmarkItems,
    status: bookmarkStatus,
  } = useBookmarkList();
  const { state, activate } = useSessionState();
  const activeId = state?.active_id ?? null;
  const toggleBookmark = useBookmarks((s) => s.toggle);

  // Mutual exclusion — opening one sidebar closes the other. Done here (not
  // inside the zustand stores) so there's no circular import between the two
  // hooks. Each effect watches its own store's open flag and closes the other.
  useEffect(() => {
    if (open) closeBookmarkSidebar();
  }, [open, closeBookmarkSidebar]);
  useEffect(() => {
    if (bookmarkOpen) closeSidebar();
  }, [bookmarkOpen, closeSidebar]);

  // Mount-time hydrate of the bookmark id set — fire-and-forget; the global
  // star state on every card depends on this being up to date.
  useEffect(() => {
    void useBookmarks.getState().hydrate();
  }, []);

  // Delete a conversation. Optimistic: drop the row from the sidebar
  // immediately so the affordance feels instant. On error, refetch — the row
  // snaps back so the user sees that the delete didn't take. On success,
  // refetch reconciles (a parallel new conversation could have arrived).
  // If the deleted thread is the ACTIVE one, auto-create a fresh conversation
  // so the UI isn't left pointing at a 404'd thread.
  const handleDelete = useCallback(
    async (id: string) => {
      const wasActive = id === conversationId;
      removeOptimistically(id);
      try {
        await deleteConversation(id);
        if (wasActive) {
          onNewConversation();
        }
      } catch (err) {
        console.error("Failed to delete conversation", err);
      } finally {
        refetch();
      }
    },
    [conversationId, removeOptimistically, refetch, onNewConversation],
  );

  // Layout heights — when the bookmark sidebar is open, the card strip
  // collapses and the map expands to fill the freed space. The
  // `transition-[height]` class on the two sections smoothly interpolates
  // between the two states. MapLibre's internal ResizeObserver keeps the GL
  // canvas in sync during the transition.
  const mapPct = bookmarkOpen ? 100 : TOP_PCT;
  const stripPct = bookmarkOpen ? 0 : 100 - TOP_PCT;

  return (
    <>
      <div className="grid h-screen w-screen grid-cols-[2fr_3fr] overflow-hidden bg-paper">
        <aside className="overflow-hidden border-r border-paper-rule">
          <ChatPane />
        </aside>
        <main className="relative h-full overflow-hidden bg-paper">
          <section
            className="absolute inset-x-0 top-0 overflow-hidden border-b border-paper-rule transition-[height] duration-300 ease-snap"
            style={{ height: `${mapPct}%` }}
          >
            <MapPane />
          </section>
          <section
            className="absolute inset-x-0 bottom-0 overflow-hidden transition-[height] duration-300 ease-snap"
            style={{ height: `${stripPct}%` }}
          >
            <CardsPane />
          </section>
        </main>
      </div>
      <ConversationSidebar
        open={open}
        items={items}
        status={status}
        activeId={conversationId}
        onClose={closeSidebar}
        onSwitch={onSwitchConversation}
        onNewChat={onNewConversation}
        onDelete={handleDelete}
      />
      <BookmarkSidebar
        open={bookmarkOpen}
        items={bookmarkItems}
        status={bookmarkStatus}
        activeId={activeId}
        onClose={closeBookmarkSidebar}
        onSelect={(id) => void activate(id)}
        onRemove={(id) => void toggleBookmark(id)}
      />
    </>
  );
}

export default App;
