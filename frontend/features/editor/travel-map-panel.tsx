"use client";

import { useEffect, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { TravelRouteWebGLPreview, type TravelRoutePoint } from "@/features/editor/travel-route-webgl-preview";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import type { TimelineClipInput } from "@/types/timeline";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type TravelMapRecord = { status: "idle" | "queued" | "processing" | "completed" | "failed"; clip?: TimelineClipInput; route: TravelRoutePoint[]; vehicle?: "plane" | "car"; error?: string; };

/** One click: narration/location text -> server-only geocoding -> animated B-Roll and original route stinger. */
export function TravelMapPanel({ timelineId, userId, sourceAssetId, playheadTime }: { timelineId: string; userId: string; sourceAssetId?: string; playheadTime: number }) {
  const upsertBRollClips = useTimelineStore((state) => state.upsertBRollClips); const [routeText, setRouteText] = useState("出發去雷克雅維克，抵達台北"); const [vehicle, setVehicle] = useState<"plane" | "car">("plane"); const [record, setRecord] = useState<TravelMapRecord>({ status: "idle", route: [] });
  const refresh = async () => { const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/travel-map`); if (!response.ok) return; const next = await response.json() as TravelMapRecord; setRecord(next); if (next.status === "completed" && next.clip) upsertBRollClips([next.clip]); };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => { if (record.status !== "queued" && record.status !== "processing") return; const id = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(id); }, [record.status]);
  const generate = async () => { setRecord((current) => ({ ...current, status: "queued", error: undefined })); try { const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/travel-map`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ route_text: routeText || undefined, source_asset_id: sourceAssetId, timeline_start: playheadTime, duration_seconds: 4, aspect_ratio: "9:16", vehicle }) }); const data = await response.json() as { detail?: string }; if (!response.ok) throw new Error(data.detail ?? "無法建立旅行地圖任務"); } catch (error) { setRecord((current) => ({ ...current, status: "failed", error: error instanceof Error ? error.message : "無法建立旅行地圖任務" })); } };
  const working = record.status === "queued" || record.status === "processing";
  return <section className="rounded-xl border border-sky-300/20 bg-slate-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-sky-50">一鍵 3D 旅遊地圖路線</h2><p className="mt-1 text-xs text-zinc-400">地名定位後，以 WebGL 預覽，並在播放頭插入動畫地圖與原創提示音。</p></div><span className="rounded bg-sky-300/10 px-2 py-1 text-[10px] font-semibold text-sky-200">{working ? "建立中" : record.status === "completed" ? "已加入" : record.status === "failed" ? "失敗" : "待建立"}</span></div><label className="mt-3 block text-xs text-zinc-300">旅行敘述／路線文字<input value={routeText} onChange={(event) => setRouteText(event.target.value)} placeholder="出發去雷克雅維克，抵達台北" className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-2 text-xs text-zinc-100 outline-none focus:border-sky-300" /></label><div className="mt-2 flex gap-2">{(["plane", "car"] as const).map((item) => <button key={item} type="button" onClick={() => setVehicle(item)} className={`rounded px-2 py-1 text-xs ${vehicle === item ? "bg-sky-300 text-slate-950" : "bg-zinc-800 text-zinc-300"}`}>{item === "plane" ? "✈️ 飛機" : "🚗 汽車"}</button>)}</div><div className="mt-3"><TravelRouteWebGLPreview route={record.route} vehicle={record.vehicle ?? vehicle} /></div><button type="button" disabled={working} onClick={() => void generate()} className="mt-3 rounded bg-sky-300 px-3 py-2 text-xs font-bold text-slate-950 disabled:opacity-45">{working ? "正在定位並渲染…" : "在播放頭加入旅行路線"}</button>{record.clip && <p className="mt-2 text-xs text-emerald-200">已插入：{record.clip.reason}</p>}{record.error && <p className="mt-2 text-xs text-rose-300">{record.error}</p>}</section>;
}
