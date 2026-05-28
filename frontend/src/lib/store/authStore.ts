"use client";
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  accessToken:  string | null;
  refreshToken: string | null;
  tenantId:     string | null;
  email:        string | null;
  role:         string | null;
  setTokens: (payload: {
    accessToken:  string;
    refreshToken: string;
    tenantId:     string;
    email:        string;
    role?:        string;
  }) => void;
  clearAuth: () => void;
  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken:  null,
      refreshToken: null,
      tenantId:     null,
      email:        null,
      role:         null,

      setTokens: ({ accessToken, refreshToken, tenantId, email, role }) =>
        set({ accessToken, refreshToken, tenantId, email, role: role ?? "analyst" }),

      clearAuth: () =>
        set({ accessToken: null, refreshToken: null, tenantId: null, email: null, role: null }),

      isAuthenticated: () => !!get().accessToken,
    }),
    { name: "evidentrx-auth" }
  )
);
