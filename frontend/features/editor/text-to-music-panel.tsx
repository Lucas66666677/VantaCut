"use client";

import { useEffect, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface MusicRecord { status: string; prompt?: string; target_duration_seconds?: number; audio_key?: string; instrumental_only?: boolean; vocals_removed?: boolean; provider_name?: string; finishing_mode?: string; error?: string; }

export function TextToMusicPanel({ timelineId, userId }: { timelineId?: string; userId?: string }) {
  const [prompt, setPrompt] = useState("適合冰川健行的電影感氛圍音樂");
  const [instrumentalOnly, setInstrumentalOnly] = useState(true);
  const [mixLevel, setMixLevel] = useState(16);
  const [provider, setProvider] = useState<"suno" | "udio">("suno");
  const [record, setRecord] = useState<MusicRecord>({ status: "idle" });
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!timelineId || !userId) return;
    const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/generated-music?user_id=${encodeURIComponent(userId)}`);
    if (response.ok) setRecord(await response.json() as MusicRecord);
  };
  useEffect(() => { void refresh(); }, [timelineId, userId]);
  useEffect(() => {
    if (!['queued', 'processing'].includes(record.status)) return;
    const timer = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(timer);
  }, [record.status, timelineId, userId]);

  const generate = async () => {
    if (!timelineId || !userId || !prompt.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/generated-music`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, prompt, instrumental_only: instrumentalOnly, mix_level: mixLevel / 100, provider }) });
      const result = await response.json() as { detail?: string; target_duration_seconds?: number };
      if (!response.ok) throw new Error(result.detail ?? "無法建立配樂任務");
      setRecord({ status: "queued", prompt, target_duration_seconds: result.target_duration_seconds, instrumental_only: instrumentalOnly });
    } catch (error) { setRecord({ status: "failed", error: error instanceof Error ? error.message : "無法建立配樂任務" }); } finally { setBusy(false); }
  };

  return <section className="rounded-xl border border-emerald-400/25 bg-zinc-950 p-4"><h2 className="text-sm font-semibold text-zinc-100">一鍵生成式配樂</h2><p className="mt-1 text-xs text-zinc-400">以目前時間軸總長度生成原創 BGM，依節拍重組並在結尾自然淡出。</p><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={2} className="mt-3 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-900 p-2 text-xs text-white outline-none focus:border-emerald-300" placeholder="描述想要的配樂氛圍…" /><div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-200"><label className="flex items-center gap-2">引擎<select value={provider} onChange={(event) => setProvider(event.target.value as "suno" | "udio")} className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-xs"><option value="suno">Suno</option><option value="udio">Udio</option></select></label><label className="flex items-center gap-2"><input type="checkbox" checked={instrumentalOnly} onChange={(event) => setInstrumentalOnly(event.target.checked)} className="accent-emerald-300" />僅保留純伴奏（偵測人聲時以 Spleeter 分離）</label><label className="flex items-center gap-2">BGM {mixLevel}%<input type="range" min="5" max="50" value={mixLevel} onChange={(event) => setMixLevel(Number(event.target.value))} className="w-20 accent-emerald-300" /></label></div><button type="button" disabled={busy || !timelineId || !userId || !prompt.trim()} onClick={() => void generate()} className="mt-3 rounded bg-emerald-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-45">{busy || record.status === "queued" ? "正在安排原創配樂…" : "生成並加入 BGM 軌"}</button>{record.status === "completed" && <p className="mt-2 text-xs text-emerald-300">已加入 {record.target_duration_seconds?.toFixed(1)} 秒 BGM{record.vocals_removed ? " · 已自動移除人聲" : ""} · {record.finishing_mode === "beat_aware_remix" ? "已依節拍重組" : "已精準循環補足"}並自然淡出。</p>}{record.status === "failed" && <p role="alert" className="mt-2 text-xs text-rose-300">{record.error}</p>}</section>;
}
