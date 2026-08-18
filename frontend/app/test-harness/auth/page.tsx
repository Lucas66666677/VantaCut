"use client";

import { AuthGate } from "@/features/auth/auth-gate";

/**
 * Playwright-only test harness — not linked from any real page or nav.
 *
 * Deliberately named app/test-harness/... (no leading underscore): Next.js's
 * App Router treats any folder starting with `_` as a "private folder"
 * excluded from routing entirely. This harness previously lived at
 * app/__test-harness__/auth and 404'd on every request — confirmed via a
 * diagnostic `curl` step added to .github/workflows/frontend-auth-checks.yml
 * that showed Next serving its own app/not-found.tsx page instead of this
 * component. That was the actual root cause of every auth-foundation.spec.ts
 * failure across several earlier CI runs, not the CORS or broken-import
 * issues fixed alongside it (those were real, independent problems, but this
 * routing exclusion masked whether they'd actually been fixed).
 *
 * The only real production entry point wrapped by AuthGate is
 * app/studio/page.tsx, via features/onboarding/studio-launchpad.tsx, which
 * statically imports features/workspace/adaptive-editor-workspace.tsx. That
 * file imports "@/features/media/local-media-bin" and
 * "@/features/media/semantic-media-bin" — two modules that have never
 * existed anywhere in this repo's git history (confirmed via `git log
 * --all`) and were already broken on the base branch before this PR (see
 * artifacts/service-readiness/deployment-execution-final.md). Because
 * Next.js must resolve a page's entire static import graph to compile that
 * route at all — in `next dev` on first visit, exactly as in `next build` —
 * /studio cannot be rendered in this environment, independent of anything
 * this PR changes and independent of AuthGate itself. Fixing that pre-
 * existing, unrelated component is out of scope here.
 *
 * This harness exercises the exact same AuthGate / AuthForm / auth-store
 * code (unchanged, not a bypass or reimplementation) through a route with no
 * dependency on that broken chain, so the auth foundation still gets real
 * browser test coverage. See frontend/e2e/auth-foundation.spec.ts for its
 * only consumer, and app/test-harness/project-status/page.tsx for the
 * identical rationale applied to the SSE/WebSocket transport tests.
 */
export default function AuthHarnessPage() {
  return (
    <AuthGate>
      <main data-testid="harness-authenticated">已登入</main>
    </AuthGate>
  );
}
