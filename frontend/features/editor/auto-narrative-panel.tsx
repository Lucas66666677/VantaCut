"use client";

import { useEffect, useMemo, useState } from "react";

import { type AutoNarrativeTone, useAutoNarrative } from "@/features/editor/use-auto-narrative";

interface AutoNarrativePanelProps {
  projectId: string;
  userId: string;
  sourceAssetIds: string[];
}

const TONES: Array<{ id: AutoNarrativeTone; label: string; description: string }> = [
  { id: "funny_vlogger", label: "幽默 Vlogger", description: "輕快、有小吐槽感" },
  { id: "emotional_vlogger", label: "感性 Vlogger", description: "溫暖、有旅程感" },
];

/** Source assets come from the current selection/timeline; parent media pickers can pass a richer list later. */
export function AutoNarrativePanel({ projectId, userId, sourceAssetIds }: AutoNarrativePanelProps) {
  const uniqueIds = useMemo(() => [...new Set(sourceAssetIds)].slice(0, 10), [sourceAssetIds]);
  const [selected, setSelected] = useState(uniqueIds);
  const [tone, setTone] = useState<AutoNarrativeTone>("funny_vlogger");
  const [duration, setDuration] = useState(30);
  const [bgmAssetId, setBgmAssetId] = useState("");
  const { generate, pending, message } = useAutoNarrative(projectId, userId);
  useEffect(() => setSelected(uniqueIds), [uniqueIds]);

  const toggle = (id: string) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 10 ? [...current, id] : current);

  return (
    <section className="rounded-xl border border-fuchsia-400/30 bg-gradient-to-br from-fuchsia-950/40 to-zinc-950 p-4 text-zinc-100">
      <h2 className="text-sm font-semibold">一鍵 AI 旁白與剪輯</h2>
      <p className="mt-1 text-xs text-zinc-400">AI 看完素材後會寫 30 秒故事、生成旁白並自動對齊畫面。</p>
      <div className="mt-3 space-y-1 rounded-lg border border-zinc-800 bg-zinc-900/70 p-2">
        <p className="text-[11px] text-zinc-400">請從目前時間軸選擇 5–10 段素材</p>
        {uniqueIds.map((id, index) => <label key={id} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs hover:bg-zinc-800"><input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} className="accent-fuchsia-300" /><span className="font-medium">素材 {index + 1}</span><span className="truncate text-zinc-500">{id}</span></label>)}
        {!uniqueIds.length && <p className="text-xs text-amber-200">先把 5–10 段素材加入時間軸，再啟動 AI 故事剪輯。</p>}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {TONES.map((item) => <button key={item.id} type="button" onClick={() => setTone(item.id)} className={`rounded-lg border p-2 text-left text-xs ${tone === item.id ? "border-fuchsia-300 bg-fuchsia-300/10" : "border-zinc-700 bg-zinc-900"}`}><span className="block font-semibold">{item.label}</span><span className="mt-1 block text-[10px] text-zinc-400">{item.description}</span></button>)}
      </div>
      <label className="mt-3 block text-xs text-zinc-300">目標長度 <b className="text-white">{duration} 秒</b><input className="mt-1 w-full accent-fuchsia-300" type="range" min="20" max="45" step="1" value={duration} onChange={(event) => setDuration(Number(event.target.value))} /></label>
      <label className="mt-3 block text-xs text-zinc-300">Lo‑Fi BGM 素材 ID（選填）<input value={bgmAssetId} onChange={(event) => setBgmAssetId(event.target.value.trim())} placeholder="不填則只輸出旁白；請使用你已上傳且擁有權利的音樂" className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-white" /></label>
      <button type="button" disabled={pending || selected.length < 5 || selected.length > 10} onClick={() => void generate({ mediaAssetIds: selected, bgmAssetId, tone, targetDurationSeconds: duration, autoRender: true })} className="mt-4 w-full rounded bg-fuchsia-300 px-3 py-2 text-sm font-bold text-zinc-950 disabled:opacity-50">{pending ? "AI 正在安排故事…" : "一鍵生成 AI 旁白 Vlog"}</button>
      {message && <p className={`mt-2 text-xs ${message.startsWith("AI 正在") ? "text-emerald-300" : "text-red-300"}`}>{message}</p>}
    </section>
  );
}
