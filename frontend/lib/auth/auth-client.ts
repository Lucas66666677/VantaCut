/**
 * Raw fetch calls against the existing backend auth routes
 * (backend/app/api/v1/auth.py, unchanged by this PR): POST /auth/register,
 * POST /auth/login, GET /auth/me. This module has no knowledge of session
 * storage or app state — see lib/auth/auth-store.ts for that — so it can be
 * called safely before any session exists (login/register never attach a
 * prior token).
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
}

export class AuthApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AuthApiError";
    this.status = status;
  }
}

async function parseAuthResponse<T>(response: Response, fallbackMessage: string): Promise<T> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // A non-JSON error body (e.g. a proxy error page) still needs to surface
    // as a generic failure below rather than throwing here.
  }
  if (!response.ok) {
    const detail = (body as { detail?: string } | null)?.detail;
    // Never echo the raw response body into the UI beyond the backend's own
    // `detail` string — and the backend's own login/register error details
    // are already deliberately generic (see auth.py), so this never leaks
    // which of email/password was wrong or whether an email is registered.
    throw new AuthApiError(response.status, typeof detail === "string" ? detail : fallbackMessage);
  }
  return body as T;
}

export async function loginRequest(email: string, password: string): Promise<string> {
  const response = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await parseAuthResponse<{ access_token: string }>(response, "登入失敗，請確認帳號密碼。");
  return body.access_token;
}

export async function registerRequest(email: string, password: string, displayName?: string): Promise<string> {
  const response = await fetch(`${API_URL}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName || undefined }),
  });
  const body = await parseAuthResponse<{ access_token: string }>(response, "註冊失敗，請稍後再試。");
  return body.access_token;
}

export async function fetchCurrentUser(token: string): Promise<AuthUser> {
  const response = await fetch(`${API_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return parseAuthResponse<AuthUser>(response, "無法驗證登入狀態。");
}
