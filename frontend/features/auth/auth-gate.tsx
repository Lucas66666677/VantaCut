"use client";

import { useEffect, type ReactNode } from "react";

import { AuthForm } from "@/features/auth/auth-form";
import { useAuthStore } from "@/lib/auth/auth-store";

/**
 * Smallest coherent protected-entry point: unauthenticated -> sign-in/create
 * -account screen, auth-loading -> loading state, authenticated -> the
 * existing app, unchanged. Wrap the entry point that needs a real session
 * with this component (currently just app/studio/page.tsx) rather than
 * gating individual components inconsistently.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStore((state) => state.status);
  const restoreSession = useAuthStore((state) => state.restoreSession);

  useEffect(() => {
    // Runs once per mount: reads sessionStorage and validates any existing
    // token against GET /auth/me exactly one time when the gate first
    // appears, so a page reload restores (or correctly drops) the session
    // before any protected UI can render.
    restoreSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (status === "loading") {
    return (
      <main className="grid min-h-screen place-items-center bg-[var(--lr-color-background)] text-sm text-[var(--lr-color-text-muted)]">
        正在確認登入狀態…
      </main>
    );
  }

  if (status === "unauthenticated") {
    return <AuthForm />;
  }

  return (
    <>
      {children}
      <LogoutControl />
    </>
  );
}

/**
 * Minimal, unobtrusive logout affordance. Kept as its own tiny overlay
 * rather than wired into the existing workspace header
 * (features/workspace/adaptive-editor-workspace.tsx) so this PR's diff stays
 * isolated to the new auth code and doesn't touch existing, unrelated
 * component internals.
 */
function LogoutControl() {
  const logout = useAuthStore((state) => state.logout);
  const email = useAuthStore((state) => state.user?.email);
  return (
    <button
      type="button"
      onClick={() => logout()}
      title={email ? `登出（${email}）` : "登出"}
      className="fixed bottom-4 right-4 z-50 rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-surface-raised)] px-3 py-2 text-xs font-medium text-[var(--lr-color-text-secondary)] shadow-[var(--lr-shadow-md)] hover:border-[var(--lr-color-border-strong)] hover:text-[var(--lr-color-text-primary)]"
    >
      登出
    </button>
  );
}
