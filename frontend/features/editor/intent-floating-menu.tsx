"use client";

import { useState } from "react";

import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import type { FaceBounds } from "@/features/editor/use-face-mesh";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export interface CanvasPoint { x: number; y: number; }

export function isPointInsideFace(point: CanvasPoint, face: FaceBounds, padding = .08) {
  if (!face.detected) return false;
  return Math.abs(point.x - face.center[0]) <= face.size[0] / 2 + padding && Math.abs(point.y - face.center[1]) <= face.size[1] / 2 + padding;
}

export function IntentFloatingMenu({ anchor, mediaAssetId, timelineId, userId, frameTime, onBeauty, onClose }: { anchor: CanvasPoint; mediaAssetId: string; timelineId?: string; userId: string; frameTime: number; onBeauty?: () => void; onClose: () => void }) {
  const begin = useOptimisticEffectsStore((state) => state.begin); const attachTask = useOptimisticEffectsStore((state) => state.attachTask); const fail = useOptimisticEffectsStore((state) => state.fail);
  const [busy, setBusy] = useState<"matting" | "tracking" | null>(null); const [message, setMessage] = useState<string | null>(null);
  const matte = async () => {
    setBusy("matting"); const id = begin({ kind: "matting", mediaAssetId, message: "已先套用人物去背預覽。" });
    try {
      const response = await fetch(`${API_URL}/api/v1/media/${mediaAssetId}/matting`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, mode: "click", frame_time: frameTime, points: [{ x: anchor.x, y: anchor.y, positive: true }], use_proxy: true, feather_pixels: 2.5, despill_strength: .65 }) });
      const result = await response.json() as { task_id?: string; detail?: string };
      if (!response.ok || !result.task_id) throw new Error(result.detail ?? "無法建立去背任務");
      attachTask(id, result.task_id); setMessage("去背預覽已套用，可以繼續剪輯。");
    } catch (error) { const detail = error instanceof Error ? error.message : "去背任務失敗"; fail(id, detail); setMessage(detail); } finally { setBusy(null); }
  };
  const track = async () => {
    if (!timelineId) { setMessage("請先儲存時間軸，再啟用主角追蹤。"); return; }
    setBusy("tracking");
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/auto-reframe`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, detector_stride: 2, smoothing: .78, max_pan_speed_px_per_second: 720 }) });
      if (!response.ok) throw new Error("無法設定主角追蹤");
      setMessage("主角追蹤已設定，直式預覽會跟隨人物。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "主角追蹤設定失敗"); } finally { setBusy(null); }
  };
  return <div data-intent-menu role="menu" onPointerDown={(event) => event.stopPropagation()} className="absolute z-50 w-44 -translate-y-full rounded-xl border border-white/15 bg-zinc-950/95 p-1.5 shadow-2xl backdrop-blur" style={{ left: `${Math.min(.78, Math.max(.02, anchor.x)) * 100}%`, top: `${Math.max(.10, anchor.y) * 100}%` }}><button type="button" disabled={busy !== null} onClick={() => void matte()} className="w-full rounded-lg px-2.5 py-2 text-left text-xs text-emerald-100 hover:bg-emerald-400/15">✂ 一鍵去背</button><button type="button" disabled={busy !== null} onClick={() => { onBeauty?.(); setMessage("美顏工具已置頂。") }} className="w-full rounded-lg px-2.5 py-2 text-left text-xs text-pink-100 hover:bg-pink-400/15">✦ 臉部美顏</button><button type="button" disabled={busy !== null} onClick={() => void track()} className="w-full rounded-lg px-2.5 py-2 text-left text-xs text-cyan-100 hover:bg-cyan-400/15">◎ 追蹤主角</button>{message && <p className="px-2 py-1.5 text-[10px] text-zinc-400">{message}</p>}<button type="button" aria-label="關閉快捷選單" onClick={onClose} className="absolute -right-2 -top-2 grid h-5 w-5 place-items-center rounded-full bg-zinc-700 text-[10px] text-white">×</button></div>;
}
