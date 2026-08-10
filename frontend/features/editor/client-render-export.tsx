"use client";

import { useState } from "react";

import { useClientRender } from "@/lib/client-render/use-client-render";
import type { ClientRenderRequest } from "@/types/client-render";
import { HookHealthPanel } from "@/features/retention/hook-health-panel";
import { ClientRenderExperience } from "@/features/editor/client-render-experience";

interface ClientRenderExportProps {
  buildRequest: (sourceFile: File) => ClientRenderRequest;
  onCloudFallback: (reason: string) => Promise<void>;
  hookContext?: { timelineId: string; userId: string; onRescued?: (timelineId: string) => void };
}

/**
 * Drop this beside the regular Export button. The ordinary server render remains the fallback,
 * while eligible short 720p/1080p edits download straight from the browser.
 */
export function ClientRenderExport({ buildRequest, onCloudFallback, hookContext }: ClientRenderExportProps) {
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const { exportTimeline, progress, thumbnail, decision, error } = useClientRender();
  const [running, setRunning] = useState(false);

  const start = async () => {
    if (!sourceFile) return;
    setRunning(true);
    try { await exportTimeline({ request: buildRequest(sourceFile), cloudFallback: onCloudFallback }); }
    finally { setRunning(false); }
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">
      {hookContext && <div className="mb-3"><HookHealthPanel timelineId={hookContext.timelineId} userId={hookContext.userId} onRescued={hookContext.onRescued} /></div>}
      <label className="flex items-center gap-2">本機來源檔
        <input type="file" accept="video/*" onChange={(event) => setSourceFile(event.target.files?.[0] ?? null)} />
      </label>
      <button disabled={!sourceFile || running} onClick={start} className="mt-2 rounded bg-emerald-500 px-3 py-1.5 font-medium text-zinc-950 disabled:opacity-40">
        {running ? "正在本機導出…" : "智慧導出"}
      </button>
      <ClientRenderExperience progress={progress} thumbnail={thumbnail} active={running} />
      {decision && <p className="mt-1 text-zinc-500">{decision.route === "client" ? "瀏覽器處理" : "雲端處理"}：{decision.reason}</p>}
      {error && <p className="mt-1 text-amber-300">{error}</p>}
    </div>
  );
}
