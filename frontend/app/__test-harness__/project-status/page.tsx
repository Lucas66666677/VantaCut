"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { AuthGate } from "@/features/auth/auth-gate";
import { useProjectStatus } from "@/features/project-status/use-project-status";

/**
 * Playwright-only test harness — not linked from any real page or nav.
 *
 * It exists because no real page in the app currently wires a live
 * `projectId` end-to-end into `useProjectStatus` (that wiring belongs to the
 * separate, later 65-file SPOOFABLE_USER_ID route migration — see
 * artifacts/service-readiness/vantacut-auth-route-map.md; even
 * `features/onboarding/studio-launchpad.tsx` doesn't pass a projectId to the
 * workspace today). This is the smallest way to give the SSE/WebSocket
 * transport rewrite (features/project-status/use-project-status.ts) real
 * browser test coverage — through the real AuthGate/auth-store integration,
 * not a bypass of it — without fabricating unrelated project-creation UI.
 * See frontend/e2e/project-status-transport.spec.ts for its only consumer.
 */
export default function ProjectStatusHarnessPage() {
  return (
    <Suspense fallback={null}>
      <AuthGate>
        <HarnessContent />
      </AuthGate>
    </Suspense>
  );
}

function HarnessContent() {
  const params = useSearchParams();
  const projectId = params.get("projectId");
  const transport = params.get("transport") === "websocket" ? "websocket" : "sse";
  const status = useProjectStatus(projectId, transport);
  return (
    <main style={{ padding: 16, fontFamily: "monospace" }}>
      <p data-testid="harness-connected">{String(status?.connected ?? false)}</p>
      <pre data-testid="harness-status">{JSON.stringify(status ?? null)}</pre>
    </main>
  );
}
