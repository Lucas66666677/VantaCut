"use client";

import { useEffect, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import type { TimelineClipInput } from "@/types/timeline";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
interface SyncRecord { status: "idle" | "queued" | "processing" | "completed" | "failed"; offset_seconds?: number; confidence?: number; audio_clip?: TimelineClipInput; error?: string; }

/** User drops an uploaded recorder asset onto the audio lane, then lets the Worker calculate its offset. */
export function AudioSyncPanel({ timelineId, userId, videoAssetId, playheadTime }: { timelineId: string; userId: string; videoAssetId?: string; playheadTime: number }) {
  const addExternalAudioClip = useTimelineStore((state) => state.addExternalAudioClip); const upsertSyncedAudioClip = useTimelineStore((state) => state.upsertSyncedAudioClip); const muteSourceAssetAudio = useTimelineStore((state) => state.muteSourceAssetAudio);
  const [externalAudioId, setExternalAudioId] = useState(""); const [record, setRecord] = useState<SyncRecord>({ status: "idle" });
  const refresh = async () => { const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/audio-sync?user_id=${encodeURIComponent(userId)}`); if (!response.ok) return; const next = await response.json() as SyncRecord; setRecord(next); if (next.status === "completed" && next.audio_clip) { upsertSyncedAudioClip(next.audio_clip); if (videoAssetId) muteSourceAssetAudio(videoAssetId); } };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => { if (record.status !== "queued" && record.status !== "processing") return; const id = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(id); }, [record.status]);
  const sync = async () => { if (!videoAssetId || !externalAudioId) return; setRecord({ status: "queued" }); try { const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/audio-sync`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, video_asset_id: videoAssetId, external_audio_asset_id: externalAudioId, max_offset_seconds: 120 }) }); const data = await response.json() as { detail?: string }; if (!response.ok) throw new Error(data.detail ?? "無法建立音畫同步任務"); } catch (error) { setRecord({ status: "failed", error: error instanceof Error ? error.message : "無法建立音畫同步任務" }); } };
  const working = record.status === "queued" || record.status === "processing";
  return (
    <section className="rounded-xl border border-cyan-300/25 bg-zinc-950 p-4">
      <div className="flex items-start justify-between gap-3">
        <div><h2 className="text-sm font-semibold text-cyan-100">一鍵音畫完美同步</h2><p className="mt-1 text-xs text-zinc-400">拖曳高音質收音到音訊軌，再以 FFT 交叉比對自動吸附；導出時相機原聲會被取代。</p></div>
        <span className="rounded bg-cyan-300/10 px-2 py-1 text-[10px] font-semibold text-cyan-200">{working ? "同步中" : record.status === "completed" ? "已對齊" : record.status === "failed" ? "失敗" : "待同步"}</span>
      </div>
      <label className="mt-3 block text-xs text-zinc-300">高音質音軌 Asset ID
        <input value={externalAudioId} onChange={(event) => setExternalAudioId(event.target.value.trim())} placeholder="上傳完成後的 Audio Asset UUID" className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-2 text-xs text-zinc-100" />
      </label>
      <button type="button" draggable={Boolean(externalAudioId)} onDragStart={(event) => event.dataTransfer.setData("application/x-external-audio", externalAudioId)} onClick={() => externalAudioId && addExternalAudioClip(externalAudioId, playheadTime)} className="mt-2 cursor-grab rounded border border-cyan-300/50 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 active:cursor-grabbing disabled:opacity-40" disabled={!externalAudioId}>拖曳此高音質音軌到「SFX／音訊覆蓋」軌</button>
      <button type="button" disabled={!videoAssetId || !externalAudioId || working} onClick={() => void sync()} className="ml-2 mt-2 rounded bg-cyan-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-45">{working ? "FFT 對齊中…" : "自動同步"}</button>
      {record.status === "completed" && <p className="mt-2 text-xs text-emerald-200">已吸附 {record.offset_seconds !== undefined ? `${record.offset_seconds >= 0 ? "+" : ""}${record.offset_seconds.toFixed(3)}s` : ""} · 信心度 {Math.round((record.confidence ?? 0) * 100)}% · 原始相機音軌已靜音。</p>}
      {record.error && <p className="mt-2 text-xs text-rose-300">{record.error}</p>}
    </section>
  );
}
