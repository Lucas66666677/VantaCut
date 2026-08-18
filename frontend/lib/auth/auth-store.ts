"use client";

/**
 * Minimal auth foundation store (zustand, matching the pattern already used
 * elsewhere in this app — see features/project-status/project-status-store.ts).
 *
 * `status` is intentionally three-valued (`loading` / `unauthenticated` /
 * `authenticated`) rather than a plain boolean so that AuthGate
 * (features/auth/auth-gate.tsx) never renders protected application state
 * as authenticated before session restoration (POST-reload token ->
 * GET /auth/me validation) has actually completed.
 */

import { create } from "zustand";

import { AuthApiError, fetchCurrentUser, loginRequest, registerRequest, type AuthUser } from "@/lib/auth/auth-client";
import { clearStoredToken, readStoredToken, writeStoredToken } from "@/lib/auth/token-storage";

export type AuthStatus = "loading" | "unauthenticated" | "authenticated";

interface AuthState {
  status: AuthStatus;
  token: string | null;
  user: AuthUser | null;
  error: string | null;
  restoreSession: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => void;
  handleUnauthorized: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  status: "loading",
  token: null,
  user: null,
  error: null,

  restoreSession: async () => {
    const token = readStoredToken();
    if (!token) {
      set({ status: "unauthenticated", token: null, user: null });
      return;
    }
    try {
      const user = await fetchCurrentUser(token);
      set({ status: "authenticated", token, user, error: null });
    } catch {
      // Missing, expired, malformed, or disabled-user token: in every case
      // the only safe move is to drop it and fall back to the sign-in
      // screen, never to render the app as authenticated on a guess.
      clearStoredToken();
      set({ status: "unauthenticated", token: null, user: null });
    }
  },

  login: async (email, password) => {
    set({ error: null });
    try {
      const token = await loginRequest(email, password);
      const user = await fetchCurrentUser(token);
      writeStoredToken(token);
      set({ status: "authenticated", token, user, error: null });
    } catch (cause) {
      const message = cause instanceof AuthApiError ? cause.message : "登入失敗，請稍後再試。";
      set({ error: message });
      throw cause;
    }
  },

  register: async (email, password, displayName) => {
    set({ error: null });
    try {
      const token = await registerRequest(email, password, displayName);
      const user = await fetchCurrentUser(token);
      writeStoredToken(token);
      set({ status: "authenticated", token, user, error: null });
    } catch (cause) {
      const message = cause instanceof AuthApiError ? cause.message : "註冊失敗，請稍後再試。";
      set({ error: message });
      throw cause;
    }
  },

  logout: () => {
    clearStoredToken();
    set({ status: "unauthenticated", token: null, user: null, error: null });
  },

  handleUnauthorized: () => {
    // Called by the authenticated fetch helper and by the project-status
    // transports when the backend reports the stored token itself is no
    // longer valid (expired/invalid, or the account was disabled) — as
    // opposed to a single request being denied for an unrelated reason
    // (e.g. a 404 for a project this user doesn't own), which must NOT log
    // the user out. See use-project-status.ts for why that distinction
    // matters for the WebSocket transport specifically.
    clearStoredToken();
    set({ status: "unauthenticated", token: null, user: null });
  },
}));
