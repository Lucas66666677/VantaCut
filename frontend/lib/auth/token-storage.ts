/**
 * Access-token persistence for the minimal frontend auth foundation.
 *
 * Storage choice: `sessionStorage`, not `localStorage` or an in-memory
 * variable. Reasoning (see artifacts/service-readiness/deployment-execution-final.md
 * for the full writeup): the backend already issues a plain bearer JWT
 * (POST /auth/login, app/api/v1/auth.py) rather than an HttpOnly cookie, so
 * moving to cookie-based sessions would be a backend architecture change
 * that is explicitly out of scope here. Pure in-memory storage would log
 * users out on every page reload, which is a worse experience than the risk
 * it avoids. `sessionStorage` survives reloads within the same tab/session
 * without persisting indefinitely across browser restarts the way
 * `localStorage` would.
 *
 * This is a known, intentional tradeoff, not a final answer: any token
 * reachable from JavaScript is XSS-sensitive by nature. A future
 * HttpOnly-cookie/session architecture may be preferable for production
 * hardening, but that is a larger, separate change (refresh tokens, CSRF
 * protection, backend session storage) tracked as future hardening, not
 * built here.
 *
 * Only the access token itself is ever stored under this key — never a
 * password, password hash, refresh token, or user object.
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";

export function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    // sessionStorage can throw (private-browsing / storage-disabled
    // contexts); treat that the same as "no session" rather than crashing.
    return null;
  }
}

export function writeStoredToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // Best-effort only. A failed write just means the session won't survive
    // a reload; the in-memory auth store state still works for this tab.
  }
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Nothing to clean up if storage is unavailable.
  }
}
