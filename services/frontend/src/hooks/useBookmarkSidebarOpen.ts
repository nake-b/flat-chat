import { create } from "zustand";

// Bookmark-sidebar open/closed state. Same shape as useSidebarOpen so the
// folder+star header button, the panel, and the backdrop each subscribe
// without prop-drilling.
//
// Mutual exclusion with the conversation sidebar is coordinated in App.tsx
// via two effects (one watching each store) — not here, to avoid a circular
// zustand-import between this file and useSidebarOpen.

interface BookmarkSidebarStore {
  open: boolean;
  openSidebar: () => void;
  closeSidebar: () => void;
  toggleSidebar: () => void;
}

export const useBookmarkSidebarOpen = create<BookmarkSidebarStore>((set) => ({
  open: false,
  openSidebar: () => set({ open: true }),
  closeSidebar: () => set({ open: false }),
  toggleSidebar: () => set((s) => ({ open: !s.open })),
}));
