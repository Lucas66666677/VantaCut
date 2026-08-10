import { Suspense } from "react";

import { MobilePreviewClient } from "@/features/editor/mobile-preview-client";

export default function MobilePreviewPage() {
  return <Suspense fallback={<main className="grid min-h-screen place-items-center bg-zinc-950 text-zinc-200">正在開啟預覽…</main>}><MobilePreviewClient /></Suspense>;
}

