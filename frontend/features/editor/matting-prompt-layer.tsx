"use client";

import { useState, type PointerEvent } from "react";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";

type PromptPoint = { x: number; y: number; positive: boolean };

interface MattingPromptLayerProps {
  mediaAssetId: string;
  userId: string;
  currentTimeMs: number;
  /** Use the same visible bounds as the proxy canvas, not the full editor panel. */
  className?: string;
  onQueued?: (result: { task_id: string; status_sse_path: string }) => void;
}

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Overlay this on the preview canvas. Click = foreground, Shift+click = exclusion point.
 * The request remains source-timestamped so a later server render is independent of viewport size.
 */
export function MattingPromptLayer({ mediaAssetId, userId, currentTimeMs, className, onQueued }: MattingPromptLayerProps) {
  const [points, setPoints] = useState<PromptPoint[]>([]);
  const [textPrompt, setTextPrompt] = useState("");
  const [mode, setMode] = useState<"click" | "text">("click");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const begin = useOptimisticEffectsStore((state) => state.begin); const attachTask = useOptimisticEffectsStore((state) => state.attachTask); const fail = useOptimisticEffectsStore((state) => state.fail);

  const addPoint = (event: PointerEvent<HTMLDivElement>) => {
    if (mode !== "click" || pending) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const point = {
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
      positive: !event.shiftKey,
    };
    setPoints((current) => [...current.slice(-15), point]);
  };

  const submit = async () => {
    setPending(true); setError(null);
    const optimisticId = begin({ kind: "matting", mediaAssetId, message: "已先套用去背預覽，AI 正在細化遮罩。" });
    try {
      const response = await fetch(`${apiBase}/api/v1/media/${mediaAssetId}/matting`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          mode,
          frame_time: currentTimeMs / 1000,
          points,
          text_prompt: mode === "text" ? textPrompt : undefined,
          use_proxy: true,
          feather_pixels: 2.5,
          despill_strength: .65,
        }),
      });
      const result = await response.json() as { task_id?: string; status_sse_path?: string; detail?: string };
      if (!response.ok || !result.task_id || !result.status_sse_path) throw new Error(result.detail ?? "Unable to queue video matting");
      attachTask(optimisticId, result.task_id);
      onQueued?.({ task_id: result.task_id, status_sse_path: result.status_sse_path });
      setPoints([]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Unable to queue video matting"; fail(optimisticId, message); setError(message);
    } finally {
      setPending(false);
    }
  };

  return (
    <div onPointerDown={addPoint} className={className ?? "absolute inset-0 cursor-crosshair"}>
      {points.map((point, index) => (
        <span
          key={`${point.x}-${point.y}-${index}`}
          className={`pointer-events-none absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${point.positive ? "border-emerald-300 bg-emerald-500/30" : "border-rose-300 bg-rose-500/30"}`}
          style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
        />
      ))}
      <div onPointerDown={(event) => event.stopPropagation()} className="absolute left-3 top-3 flex max-w-sm gap-2 rounded-lg bg-black/70 p-2 text-xs text-white backdrop-blur">
        <select value={mode} onChange={(event) => setMode(event.target.value as "click" | "text")} className="rounded bg-zinc-800 px-2 py-1">
          <option value="click">點擊追蹤</option><option value="text">文字語意</option>
        </select>
        {mode === "text" ? <input value={textPrompt} onChange={(event) => setTextPrompt(event.target.value)} placeholder="例如：背景的天空" className="min-w-40 rounded bg-zinc-800 px-2 py-1" /> : <span className="self-center text-zinc-300">點擊前景；Shift+點擊排除</span>}
        <button type="button" disabled={pending || (mode === "click" ? !points.some((point) => point.positive) : textPrompt.trim().length < 2)} onClick={() => void submit()} className="rounded bg-violet-500 px-2 py-1 font-medium disabled:opacity-50">
          {pending ? "排程中…" : "開始摳像"}
        </button>
      </div>
      {error && <p className="absolute bottom-3 left-3 rounded bg-rose-950/90 px-3 py-2 text-xs text-rose-100">{error}</p>}
    </div>
  );
}
