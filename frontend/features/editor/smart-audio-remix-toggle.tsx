"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type RemixStatus = "idle" | "queued" | "processing" | "completed" | "disabled" | "failed";

/** A single toggle: the backend uses the timeline's current BGM unless a caller supplies one. */
export function SmartAudioRemixToggle({ timelineId, userId }: { timelineId: string; userId: string }) {
  const [record, setRecord] = useState<{ status: RemixStatus; target_duration_seconds?: number; bpm?: number; error?: string }>({ status: "idle" });
  const refresh = async () => {
    const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/smart-audio-remix?user_id=${encodeURIComponent(userId)}`);
    if (response.ok) setRecord(await response.json() as { status: RemixStatus; target_duration_seconds?: number; bpm?: number; error?: string });
  };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => {
    if (record.status !== "queued" && record.status !== "processing") return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [record.status]);
  const enabled = record.status === "completed";
  const apply = async () => {
    if (record.status === "queued" || record.status === "processing") return;
    if (enabled) {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/smart-audio-remix?user_id=${encodeURIComponent(userId)}`, { method: "DELETE" });
      if (response.ok) setRecord(await response.json() as { status: RemixStatus; target_duration_seconds?: number; bpm?: number; error?: string });
      return;
    }
    setRecord({ status: "queued" });
    const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/smart-audio-remix`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, mix_level: .16 }) });
    if (!response.ok) { const body = await response.json() as { detail?: string }; setRecord({ status: "failed", error: body.detail ?? "無法啟動智慧音樂重混" }); }
  };
  const busy = record.status === "queued" || record.status === "processing";
  return <section className="rounded-xl border border-sky-400/25 bg-zinc-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">智慧音樂重混</h2><p className="mt-1 text-xs text-zinc-400">從目前 BGM 擷取前奏＋副歌＋尾奏，依影片長度重新拼接並在結尾自然收束。</p></div><button type="button" role="switch" aria-checked={enabled} disabled={busy} onClick={() => void apply()} className={`relative h-7 w-12 rounded-full transition ${enabled ? "bg-sky-400" : "bg-zinc-700"} disabled:opacity-60`}><span className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${enabled ? "left-6" : "left-1"}`} /></button></div><button type="button" disabled={busy} onClick={() => void apply()} className="mt-3 rounded bg-sky-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-45">{busy ? "正在重編 BGM…" : enabled ? "關閉智慧重混" : "🎵 智慧適應影片長度"}</button>{record.status === "completed" && <p className="mt-2 text-xs text-emerald-300">已生成 {record.target_duration_seconds?.toFixed(1)} 秒重混 BGM · {record.bpm?.toFixed(0)} BPM。</p>}{record.error && <p role="alert" className="mt-2 text-xs text-rose-300">{record.error}</p>}</section>;
}
