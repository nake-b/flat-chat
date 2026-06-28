import { create } from "zustand";

import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  type AuthUser,
} from "../api/auth";

// Client-local auth state (zustand singleton, same pattern as useHover).
//   loading — initial session check in flight
//   authed  — valid cookie, `user` populated
//   anon    — no session; LoginGate renders the form
type AuthStatus = "loading" | "authed" | "anon";

interface AuthStore {
  status: AuthStatus;
  user: AuthUser | null;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthStore>((set) => ({
  status: "loading",
  user: null,

  refresh: async () => {
    try {
      const user = await fetchMe();
      set(user ? { status: "authed", user } : { status: "anon", user: null });
    } catch {
      set({ status: "anon", user: null });
    }
  },

  login: async (email, password) => {
    await apiLogin(email, password);
    const user = await fetchMe();
    set(user ? { status: "authed", user } : { status: "anon", user: null });
  },

  logout: async () => {
    await apiLogout();
    set({ status: "anon", user: null });
  },
}));
