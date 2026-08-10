"use client";

import { useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { SPEED_CURVE_PRESETS, graphYToSpeed, speedToGraphY, type ClipSpeedCurve, type SpeedCurvePoint, type SpeedCurvePreset } from "@/types/speed-curves";
import type { TimelineClip } from "@/types/timeline";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const GRAPH_WIDTH = 260; const GRAPH_HEIGHT = 116;

interface SpeedCurvePanelProps { clip: TimelineClip; timelineId?: string; userId?: string; }

function curvePath(points: SpeedCurvePoint[]) { return points.map((point, index) => `${index ? "L" : "M"} ${point.position * GRAPH_WIDTH} ${speedToGraphY(point.speed) * GRAPH_HEIGHT}`).join(" "); }

export function SpeedCurvePanel({ clip, timelineId, userId }: SpeedCurvePanelProps) {
  const saved = useTimelineStore((state) => state.speedCurves[clip.id]); const setSpeedCurve = useTimelineStore((state) => state.setSpeedCurve);
  const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null);
  const curve = saved ?? { clip_id: clip.id, preset: "custom" as SpeedCurvePreset, points: [{ position: 0, speed: 1 }, { position: 1, speed: 1 }] };
  const setCurve = (next: ClipSpeedCurve) => setSpeedCurve(next, clip.id);
  const applyPreset = (preset: Exclude<SpeedCurvePreset, "custom">) => setCurve({ clip_id: clip.id, preset, points: structuredClone(SPEED_CURVE_PRESETS[preset].points) });
  const changePoint = (index: number, speed: number) => setCurve({ ...curve, preset: "custom", points: curve.points.map((point, pointIndex) => pointIndex === index ? { ...point, speed } : point) });
  const persist = async () => {
    if (!timelineId || !userId) { setError("請使用已儲存的 Timeline 後再套用變速。"); return; }
    setPending(true); setError(null);
    try { const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/speed-curves`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, curves: [curve] }) }); if (!response.ok) throw new Error("無法儲存速度曲線"); } catch (cause) { setError(cause instanceof Error ? cause.message : "無法儲存速度曲線"); } finally { setPending(false); }
  };
  return <section className="mt-4 border-t border-zinc-800 pt-3"><h3 className="text-xs font-semibold text-zinc-100">一鍵曲線變速</h3><div className="mt-2 grid grid-cols-3 gap-1">{(Object.keys(SPEED_CURVE_PRESETS) as Array<Exclude<SpeedCurvePreset, "custom">>).map((preset) => <button key={preset} type="button" onClick={() => applyPreset(preset)} className={`rounded border px-2 py-1.5 text-[10px] ${curve.preset === preset ? "border-amber-300 bg-amber-300/10 text-amber-100" : "border-zinc-700 text-zinc-300"}`}>{({ hero: "子彈時間 Hero", flash_in: "閃現 Flash In", montage: "跳動 Montage" })[preset]}</button>)}</div><div className="mt-3 rounded-lg border border-zinc-700 bg-zinc-950 p-2"><div className="mb-1 flex justify-between text-[10px] text-zinc-500"><span>10x</span><span>1x</span><span>0.1x</span></div><svg viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`} className="h-32 w-full touch-none" aria-label="可拖曳速度折線圖"><path d="M 0 0 V 116 M 0 58 H 260 M 0 116 H 260" stroke="#3f3f46" strokeDasharray="3 3" fill="none" /><path d={curvePath(curve.points)} stroke="#fbbf24" strokeWidth="3" fill="none" />{curve.points.map((point, index) => <circle key={index} cx={point.position * GRAPH_WIDTH} cy={speedToGraphY(point.speed) * GRAPH_HEIGHT} r="6" fill="#fde68a" stroke="#18181b" strokeWidth="2" className="cursor-ns-resize" onPointerDown={(event) => { const svg = event.currentTarget.ownerSVGElement; if (!svg) return; event.currentTarget.setPointerCapture(event.pointerId); const move = (moveEvent: PointerEvent) => { const rect = svg.getBoundingClientRect(); changePoint(index, Number(graphYToSpeed((moveEvent.clientY - rect.top) / rect.height).toFixed(2))); }; const cleanup = () => { event.currentTarget.removeEventListener("pointermove", move); event.currentTarget.removeEventListener("pointerup", cleanup); event.currentTarget.removeEventListener("pointercancel", cleanup); }; event.currentTarget.addEventListener("pointermove", move); event.currentTarget.addEventListener("pointerup", cleanup); event.currentTarget.addEventListener("pointercancel", cleanup); }} />)}</svg><p className="mt-1 text-[10px] text-zinc-400">上下拖曳節點微調 0.1x–10x；低於 0.5x 導出時自動啟用光流補幀。</p></div><button type="button" disabled={pending} onClick={() => void persist()} className="mt-2 w-full rounded bg-amber-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "儲存中…" : "套用速度曲線"}</button>{error && <p className="mt-1 text-[10px] text-red-300">{error}</p>}</section>;
}

