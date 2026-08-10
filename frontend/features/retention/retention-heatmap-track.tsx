"use client";

import { useState } from "react";

import type { RetentionHotspot, RetentionPrediction } from "@/types/retention";

function heatColor(risk: number): string {
  if (risk < 28) return "#22c55e";
  if (risk < 55) return "#eab308";
  if (risk < 75) return "#f97316";
  return "#ef4444";
}

function formatTime(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

interface RetentionHeatmapTrackProps {
  prediction?: RetentionPrediction;
  duration: number;
  zoom: number;
}

export function RetentionHeatmapTrack({ prediction, duration, zoom }: RetentionHeatmapTrackProps) {
  const [activeHotspot, setActiveHotspot] = useState<RetentionHotspot>();
  if (!prediction || !prediction.curve.length) return null;
  const safeDuration = Math.max(1, duration);
  const gradient = prediction.curve
    .map((point) => `${heatColor(point.risk_score)} ${Math.min(100, point.time_seconds / safeDuration * 100).toFixed(2)}%`)
    .join(", ");

  return (
    <div className="relative mt-2 h-12 border-t border-zinc-800 bg-zinc-950/80 px-2 pt-5" onPointerDown={(event) => event.stopPropagation()}>
      <span className="absolute left-2 top-1 z-10 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-300">預測留存熱區</span>
      <span className={`absolute right-2 top-1 text-[10px] ${prediction.is_calibrated ? "text-emerald-300" : "text-amber-300"}`}>
        {prediction.is_calibrated ? "已校正模型" : "未校正預測"}
      </span>
      <div className="h-4 rounded-sm opacity-85" style={{ width: safeDuration * zoom, backgroundImage: `linear-gradient(to right, ${gradient})` }} />
      {prediction.hotspots.map((hotspot) => {
        const left = hotspot.start_time * zoom;
        const width = Math.max(8, (hotspot.end_time - hotspot.start_time) * zoom);
        const open = activeHotspot?.id === hotspot.id;
        return (
          <button
            key={hotspot.id}
            aria-label={`留存風險：${hotspot.reason}`}
            className="absolute top-5 h-4 rounded-sm border border-red-100/80 bg-red-500/35 hover:bg-red-400/55 focus:outline-none focus:ring-2 focus:ring-red-200"
            style={{ left, width }}
            onMouseEnter={() => setActiveHotspot(hotspot)}
            onMouseLeave={() => setActiveHotspot(undefined)}
            onFocus={() => setActiveHotspot(hotspot)}
            onBlur={() => setActiveHotspot(undefined)}
            onClick={() => setActiveHotspot(open ? undefined : hotspot)}
          >
            <span className="sr-only">預估 {hotspot.predicted_drop.toFixed(1)}% 流失</span>
            {open && (
              <span className="absolute bottom-6 left-0 z-50 w-72 rounded-lg border border-red-400/50 bg-zinc-900 p-3 text-left text-xs text-zinc-100 shadow-2xl">
                <strong className="block text-red-200">預估 {hotspot.predicted_drop.toFixed(1)}% 流失 · {formatTime(hotspot.start_time)}–{formatTime(hotspot.end_time)}</strong>
                <span className="mt-1 block text-zinc-300">{hotspot.reason}</span>
                <span className="mt-2 block text-amber-200">{hotspot.suggestion}</span>
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
