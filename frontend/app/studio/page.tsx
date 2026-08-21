import { Suspense } from "react";

import { AuthGate } from "@/features/auth/auth-gate";
import { StudioLaunchpad } from "@/features/onboarding/studio-launchpad";

export default function StudioPage() {
  return (
    <AuthGate>
      <Suspense fallback={<main className="grid min-h-screen place-items-center bg-[var(--lr-color-background)] text-sm text-[var(--lr-color-text-muted)]">正在準備你的工作室…</main>}>
        <StudioLaunchpad />
      </Suspense>
    </AuthGate>
  );
}
