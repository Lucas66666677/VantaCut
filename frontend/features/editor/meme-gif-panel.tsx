"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Status = "idle" | "queued" | "processing" | "completed" | "failed";
interface MemeEvent { id: string; reason: string; timeline_start: number; status: "ready" | "suggested"; source_url?: string; error?: string; }

/** Server-side GIF discovery keeps API keys private and leaves every proposed insert reviewable. */
export function MemeGifPanel({ timelineId, userId, sourceAssetId }: { timelineId: string; userId: string; sourceAssetId?: string }) {
  const [record, setRecord] = useState<{ status: Status; events: MemeEvent[]; error?: string }>({ status: "idle", events: [] });
  const refresh = async () => {
    const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/meme-gifs?user_id=${encodeURIComponent(userId)}`);
    if (response.ok) setRecord(await response.json() as { status: Status; events: MemeEvent[]; error?: string });
  };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => {
    if (record.status !== "queued" && record.status !== "processing") return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [record.status]);
  const generate = async () => {
    if (!sourceAssetId) return;
    setRecord({ status: "queued", events: [] });
    const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/meme-gifs`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, source_asset_id: sourceAssetId, provider: "auto", insertion_mode: "overlay", max_events: 4 }),
    });
    if (!response.ok) {
      const body = await response.json() as { detail?: string };
      setRecord({ status: "failed", events: [], error: body.detail ?? "無法建立迷因任務" });
    }
  };
  const busy = record.status === "queued" || record.status === "processing";
  return <section className="rounded-xl border border-fuchsia-400/25 bg-zinc-950 p-4">
    <h2 className="text-sm font-semibold text-zinc-100">AI 迷因與 GIF 穿插</h2>
    <p className="mt-1 text-xs text-zinc-400">偵測無言停頓、嘆氣與「傻眼／Bruh／大崩潰」等語氣，建立可審閱的效果軌；來源連結會保留在專案中。</p>
    <button type="button" disabled={!sourceAssetId || busy} onClick={() => void generate()} className="mt-3 rounded bg-fuchsia-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-45">{busy ? "正在找迷因素材…" : "自動加入迷因建議"}</button>
    {!sourceAssetId && <p className="mt-2 text-xs text-amber-300">目前時間軸缺少來源影片。</p>}
    {record.events.length > 0 && <div className="mt-3 space-y-1.5">{record.events.map((event) => <div key={event.id} className="rounded bg-zinc-900 px-2 py-1.5 text-xs text-zinc-300"><span className={event.status === "ready" ? "text-emerald-300" : "text-amber-300"}>{event.status === "ready" ? "已備妥" : "僅建議"}</span> · {event.timeline_start.toFixed(1)}s · {event.reason}{event.source_url && <a className="ml-2 text-fuchsia-300 underline" href={event.source_url} target="_blank" rel="noreferrer">來源</a>}{event.error && <p className="mt-1 text-amber-300">{event.error}</p>}</div>)}</div>}
    {record.error && <p role="alert" className="mt-2 text-xs text-rose-300">{record.error}</p>}
  </section>;
}
