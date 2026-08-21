"use client";

import { useEffect, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import type { TimelineClipInput } from "@/types/timeline";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface StockBRollRecord { status: "idle" | "queued" | "processing" | "completed" | "failed"; clips: TimelineClipInput[]; error?: string; }

/** Triggers the server-side transcript -> Pexels -> MinIO pipeline and mirrors completed B-Roll into Zustand. */
export function SemanticStockBRollPanel({ timelineId, sourceAssetId }: { timelineId: string; userId: string; sourceAssetId?: string }) {
  const upsertBRollClips = useTimelineStore((state) => state.upsertBRollClips);
  const [record, setRecord] = useState<StockBRollRecord>({ status: "idle", clips: [] }); const [pending, setPending] = useState(false);
  const refresh = async () => {
    const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/b-roll/semantic-stock`);
    if (!response.ok) return;
    const next = await response.json() as StockBRollRecord; setRecord(next);
    if (next.status === "completed" || next.status === "failed") setPending(false);
    if (next.status === "completed") upsertBRollClips(next.clips);
  };
  useEffect(() => { void refresh(); }, [timelineId]);
  useEffect(() => {
    if (record.status !== "queued" && record.status !== "processing") return;
    const interval = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(interval);
  }, [pending, record.status]);
  const generate = async () => {
    if (!sourceAssetId) return;
    setPending(true); setRecord({ status: "processing", clips: [] });
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/b-roll/semantic-stock`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_asset_id: sourceAssetId, aspect_ratio: "9:16", duration_seconds: 4, max_clips: 3 }) });
      const result = await response.json() as { detail?: string }; if (!response.ok) throw new Error(result.detail ?? "無法建立語意 B-Roll 任務");
    } catch (error) { setPending(false); setRecord({ status: "failed", clips: [], error: error instanceof Error ? error.message : "無法建立語意 B-Roll 任務" }); }
  };
  return <section className="rounded-xl border border-violet-400/25 bg-zinc-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">AI 語意 B-Roll 自動覆蓋</h2><p className="mt-1 text-xs text-zinc-400">從逐字稿找出具象場景，自動插入第二軌；預設靜音、0.25 秒淡入淡出。</p></div><span className={`rounded px-2 py-1 text-[10px] font-semibold ${record.status === "completed" ? "bg-emerald-400/15 text-emerald-200" : record.status === "failed" ? "bg-rose-400/15 text-rose-200" : "bg-violet-400/15 text-violet-200"}`}>{record.status === "queued" ? "排隊中" : record.status === "processing" ? "搜尋中" : record.status === "completed" ? "已加入" : record.status === "failed" ? "失敗" : "待執行"}</span></div><button type="button" disabled={!sourceAssetId || pending || record.status === "queued" || record.status === "processing"} onClick={() => void generate()} className="mt-4 rounded bg-violet-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-45">{record.status === "queued" || record.status === "processing" ? "正在搜尋免版稅素材…" : "一鍵加入語意 B-Roll"}</button>{!sourceAssetId && <p className="mt-2 text-xs text-amber-300">目前時間軸缺少來源素材 ID，無法建立 B-Roll。</p>}{record.error && <p className="mt-2 text-xs text-rose-300">{record.error}</p>}{record.clips.length > 0 && <div className="mt-3 space-y-1.5 text-xs text-zinc-300">{record.clips.map((clip) => <div key={clip.id} className="flex items-center justify-between rounded bg-zinc-900 px-2 py-1.5"><span>{clip.reason} · {clip.timeline_start?.toFixed(1)}s</span><a href={String(clip.stock?.pexels_url ?? "https://www.pexels.com")} target="_blank" rel="noreferrer" className="text-violet-300 underline">素材來源 Pexels</a></div>)}</div>}</section>;
}
