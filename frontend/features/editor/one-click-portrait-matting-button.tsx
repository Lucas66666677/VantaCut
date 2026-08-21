"use client";

import { useState } from "react";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface OneClickPortraitMattingButtonProps {
  mediaAssetId: string;
  userId: string;
  frameTime: number;
  onQueued?: (result: { taskId: string; statusSsePath: string }) => void;
}

/** Center-biased SAM 2 prompt for the common case where the creator is the foreground subject. */
export function OneClickPortraitMattingButton({ mediaAssetId, frameTime, onQueued }: OneClickPortraitMattingButtonProps) {
  const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null);
  const begin = useOptimisticEffectsStore((state) => state.begin); const attachTask = useOptimisticEffectsStore((state) => state.attachTask); const fail = useOptimisticEffectsStore((state) => state.fail);
  const separate = async () => {
    setPending(true); setError(null);
    const optimisticId = begin({ kind: "matting", mediaAssetId, message: "已先套用去背預覽，背景正持續細化。" });
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/media/${mediaAssetId}/matting`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "click", frame_time: frameTime, points: [{ x: .5, y: .42, positive: true }], use_proxy: true, feather_pixels: 2.5, despill_strength: .65 }),
      });
      const result = await response.json() as { task_id?: string; status_sse_path?: string; detail?: string };
      if (!response.ok || !result.task_id || !result.status_sse_path) throw new Error(result.detail ?? "無法建立人物去背任務");
      attachTask(optimisticId, result.task_id);
      onQueued?.({ taskId: result.task_id, statusSsePath: result.status_sse_path });
    } catch (cause) { const message = cause instanceof Error ? cause.message : "人物去背失敗"; fail(optimisticId, message); setError(message); }
    finally { setPending(false); }
  };
  return <div className="space-y-1"><button type="button" onClick={() => void separate()} disabled={pending} className="rounded bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-emerald-950 disabled:opacity-50">{pending ? "SAM 2 處理中…" : "一鍵主角去背"}</button><p className="text-[10px] text-zinc-400">輸出透明 Alpha 圖層後，可將風景素材放在底層。</p>{error && <p role="alert" className="text-xs text-red-300">{error}</p>}</div>;
}
