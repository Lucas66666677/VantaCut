"use client";

import { useAuthStore } from "@/lib/auth/auth-store";

/**
 * Minimal authenticated fetch helper.
 *
 * The rest of the frontend (49+ call sites) still passes a plain
 * client-supplied `user_id` to the 65 SPOOFABLE_USER_ID routes — that
 * mechanical migration is explicitly out of scope for this PR (see
 * artifacts/service-readiness/vantacut-auth-route-map.md). This helper
 * exists only for the calls that need a real bearer credential today: the
 * project-status SSE fetch-stream replacement (use-project-status.ts) is
 * the current caller; it is written generically so future authenticated
 * routes can adopt it without inventing another helper.
 *
 * Attaches `Authorization: Bearer <token>` when a session exists, preserves
 * any headers/body the caller already set, and clears the session (routing
 * the app back to the sign-in screen via AuthGate) if the backend reports
 * the token is no longer valid. Do NOT route POST /auth/login or
 * POST /auth/register through this helper — see lib/auth/auth-client.ts —
 * since those calls must succeed with no prior token and must never trigger
 * a "session expired" reaction.
 */
export async function authenticatedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = useAuthStore.getState().token;
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) {
    useAuthStore.getState().handleUnauthorized();
  }
  return response;
}
