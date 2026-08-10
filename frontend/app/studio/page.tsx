import { Suspense } from "react";

import { StudioLaunchpad } from "@/features/onboarding/studio-launchpad";

export default function StudioPage() {
  return <Suspense fallback={<main className="grid min-h-screen place-items-center bg-zinc-950 text-sm text-zinc-400">正在準備你的工作室…</main>}><StudioLaunchpad /></Suspense>;
}
