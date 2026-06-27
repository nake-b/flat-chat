import { create } from "zustand";

// Conversation-list sidebar open/closed state. zustand singleton so the
// hamburger (in ChatPane), the panel (rendered by App), and the backdrop can
// each subscribe without prop-drilling through the layout shell. Same pattern
// as `useHover` — both are client-local ephemera the agent never reasons about.

interface SidebarStore {
  open: boolean;
  openSidebar: () => void;
  closeSidebar: () => void;
  toggleSidebar: () => void;
}

export const useSidebarOpen = create<SidebarStore>((set) => ({
  open: false,
  openSidebar: () => set({ open: true }),
  closeSidebar: () => set({ open: false }),
  toggleSidebar: () => set((s) => ({ open: !s.open })),
}));
