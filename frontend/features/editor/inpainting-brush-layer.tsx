"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";

type Point = { x: number; y: number };
type BrushStroke = { points: Point[]; radius: number };

interface InpaintingBrushLayerProps {
  mediaAssetId: string;
  userId: string;
  startTime: number;
  endTime: number;
  className?: string;
  onQueued?: (result: { taskId: string; statusSsePath: string }) => void;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Overlay this on the video preview: painted red pixels become the first-frame repair mask. */
export function InpaintingBrushLayer({ mediaAssetId, userId, startTime, endTime, className, onQueued }: InpaintingBrushLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [strokes, setStrokes] = useState<BrushStroke[]>([]);
  const [painting, setPainting] = useState(false);
  const [radius, setRadius] = useState(.035);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const beginOptimistic = useOptimisticEffectsStore((state) => state.begin); const attachTask = useOptimisticEffectsStore((state) => state.attachTask); const failOptimistic = useOptimisticEffectsStore((state) => state.fail);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr)); canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const context = canvas.getContext("2d"); if (!context) return;
    context.scale(dpr, dpr); context.clearRect(0, 0, rect.width, rect.height);
    context.strokeStyle = "rgba(255, 46, 71, .82)"; context.fillStyle = "rgba(255, 46, 71, .42)"; context.lineCap = "round"; context.lineJoin = "round";
    for (const stroke of strokes) {
      const pixels = Math.max(4, stroke.radius * Math.min(rect.width, rect.height) * 2);
      context.lineWidth = pixels;
      context.beginPath();
      stroke.points.forEach((point, index) => {
        const x = point.x * rect.width; const y = point.y * rect.height;
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      });
      context.stroke();
      if (stroke.points.length === 1) {
        const point = stroke.points[0]; context.beginPath(); context.arc(point.x * rect.width, point.y * rect.height, pixels / 2, 0, Math.PI * 2); context.fill();
      }
    }
  }, [strokes]);

  const pointFrom = (event: PointerEvent<HTMLCanvasElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
  };
  const begin = (event: PointerEvent<HTMLCanvasElement>) => {
    if (pending) return;
    event.currentTarget.setPointerCapture(event.pointerId); setPainting(true); setStrokes((items) => [...items, { points: [pointFrom(event)], radius }]);
  };
  const draw = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!painting || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const point = pointFrom(event);
    setStrokes((items) => items.map((stroke, index) => index === items.length - 1 ? { ...stroke, points: [...stroke.points, point] } : stroke));
  };
  const finish = (event: PointerEvent<HTMLCanvasElement>) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); setPainting(false); };
  const submit = async () => {
    if (!strokes.length) return;
    setPending(true); setError(null);
    const optimisticId = beginOptimistic({ kind: "inpainting", mediaAssetId, message: "已先隱藏路人預覽，AI 正在補齊背景。" });
    try {
      const response = await fetch(`${API_URL}/api/v1/media/${mediaAssetId}/inpaint`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, frame_time: (startTime + endTime) / 2, start_time: startTime, end_time: endTime, mask_strokes: strokes, use_proxy: true }),
      });
      const result = await response.json() as { task_id?: string; status_sse_path?: string; detail?: string };
      if (!response.ok || !result.task_id || !result.status_sse_path) throw new Error(result.detail ?? "無法建立路人消除任務");
      attachTask(optimisticId, result.task_id);
      onQueued?.({ taskId: result.task_id, statusSsePath: result.status_sse_path }); setStrokes([]);
    } catch (cause) { const message = cause instanceof Error ? cause.message : "無法建立路人消除任務"; failOptimistic(optimisticId, message); setError(message); }
    finally { setPending(false); }
  };

  return <div className={className ?? "absolute inset-0"}>
    <canvas ref={canvasRef} onPointerDown={begin} onPointerMove={draw} onPointerUp={finish} onPointerCancel={finish} className="absolute inset-0 h-full w-full touch-none cursor-crosshair" aria-label="以紅色筆刷塗抹要消除的物件" />
    <div onPointerDown={(event) => event.stopPropagation()} className="absolute left-3 top-3 flex items-center gap-2 rounded-lg bg-black/75 p-2 text-xs text-white backdrop-blur">
      <span>塗紅路人</span><input aria-label="筆刷大小" type="range" min="0.01" max="0.12" step="0.005" value={radius} onChange={(event) => setRadius(Number(event.target.value))} />
      <button type="button" onClick={() => setStrokes([])} className="rounded bg-zinc-700 px-2 py-1">清除</button>
      <button type="button" onClick={() => void submit()} disabled={pending || !strokes.length} className="rounded bg-rose-500 px-2 py-1 font-semibold disabled:opacity-50">{pending ? "排程中…" : "一鍵消除"}</button>
    </div>
    {error && <p role="alert" className="absolute bottom-3 left-3 rounded bg-rose-950/90 px-3 py-2 text-xs text-rose-100">{error}</p>}
  </div>;
}
