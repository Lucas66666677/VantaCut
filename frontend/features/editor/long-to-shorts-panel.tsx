"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Status = "idle" | "queued" | "processing" | "completed" | "rendering" | "exported" | "failed";
interface ShortCard { timeline_id: string; title: string; source_start: number; source_end: number; duration: number; score: number; }
interface Record { status: Status; shorts: ShortCard[]; source_preview_url?: string; download_url?: string; error?: string; }

/** Renders three independent short-form candidates and starts their parallel export. */
export function LongToShortsPanel({ timelineId, userId, sourceAssetId }: { timelineId: string; userId: string; sourceAssetId?: string }) {
  const [record, setRecord] = useState<Record>({ status: "idle", shorts: [] }); const [pending, setPending] = useState(false);
  const refresh = async () => {
    const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/long-to-shorts?user_id=${encodeURIComponent(userId)}`);
    if (response.ok) { const next = await response.json() as Record; setRecord(next); if (["completed", "exported", "failed", "idle"].includes(next.status)) setPending(false); }
  };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => { if (!["queued", "processing", "rendering"].includes(record.status)) return; const timer = window.setInterval(() => void refresh(), 3000); return () => window.clearInterval(timer); }, [record.status]);
  const generate = async () => {
    if (!sourceAssetId) return; setPending(true); setRecord({ status: "queued", shorts: [] });
    try { const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/long-to-shorts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, source_media_asset_id: sourceAssetId, count: 3, min_duration_seconds: 45, max_duration_seconds: 60 }) }); const body = await response.json() as { detail?: string }; if (!response.ok) throw new Error(body.detail ?? "無法建立短片任務"); } catch (error) { setPending(false); setRecord({ status: "failed", shorts: [], error: error instanceof Error ? error.message : "無法建立短片任務" }); }
  };
  const exportAll = async () => {
    setPending(true); const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/long-to-shorts/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, resolution: "1080p" }) }); if (!response.ok) { const body = await response.json() as { detail?: string }; setPending(false); setRecord((current) => ({ ...current, status: "failed", error: body.detail ?? "無法建立批量導出" })); } else setRecord((current) => ({ ...current, status: "rendering" }));
  };
  const busy = pending || ["queued", "processing", "rendering"].includes(record.status);
  return <section className="rounded-xl border border-orange-400/30 bg-zinc-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">長片一鍵轉 3 支 Shorts</h2><p className="mt-1 text-xs text-zinc-400">找出完整高資訊片段，自動改為直式跟拍、動態大字幕與口語 Hook。</p></div><span className="rounded bg-orange-300/15 px-2 py-1 text-[10px] font-semibold text-orange-200">{record.status === "exported" ? "已打包" : busy ? "處理中" : record.status === "completed" ? "可匯出" : "待建立"}</span></div><button type="button" disabled={!sourceAssetId || busy} onClick={() => void generate()} className="mt-3 rounded bg-orange-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-45">{busy && record.shorts.length === 0 ? "AI 正在挑選高光…" : "從長片產生 3 支 Shorts"}</button>{record.shorts.length > 0 && <div className="mt-4 grid gap-3 sm:grid-cols-3">{record.shorts.map((item, index) => <article key={item.timeline_id} className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900"><div className="aspect-[9/16] bg-gradient-to-br from-orange-400/30 via-zinc-800 to-zinc-950">{record.source_preview_url && <video className="h-full w-full object-cover opacity-75" muted preload="metadata" src={`${record.source_preview_url}#t=${item.source_start.toFixed(2)}`} />}</div><div className="p-2"><p className="line-clamp-2 text-xs font-bold text-white">{item.title}</p><p className="mt-1 text-[10px] text-zinc-400">{item.source_start.toFixed(1)}s–{item.source_end.toFixed(1)}s · {item.duration.toFixed(0)} 秒</p><p className="mt-1 text-[10px] text-orange-200">Auto-Reframe · 動態字幕 · Hook</p></div></article>)}</div>}{record.status === "completed" && <button type="button" disabled={busy} onClick={() => void exportAll()} className="mt-4 rounded bg-emerald-300 px-3 py-2 text-xs font-bold text-zinc-950">全部匯出並打包 ZIP</button>}{record.download_url && <a className="mt-4 inline-block rounded bg-emerald-300 px-3 py-2 text-xs font-bold text-zinc-950" href={record.download_url}>下載 3 支 Shorts ZIP</a>}{!sourceAssetId && <p className="mt-2 text-xs text-amber-300">目前時間軸沒有可分析的來源影片。</p>}{record.error && <p role="alert" className="mt-2 text-xs text-rose-300">{record.error}</p>}</section>;
}
