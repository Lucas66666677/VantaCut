"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STYLES = [
  { id: "energetic_girl", label: "元氣少女", detail: "短影音開場／活潑" },
  { id: "calm_narrator", label: "沉穩解說", detail: "知識影片／紀錄片" },
  { id: "funny_host", label: "搞怪幽默", detail: "輕鬆節奏／吐槽" },
  { id: "warm_friend", label: "暖心朋友", detail: "分享／療癒口吻" },
  { id: "cool_storyteller", label: "酷感故事家", detail: "電影感／故事敘述" },
] as const;

interface TTSNarrationPanelProps { timelineId: string; userId: string; playheadTime?: number; onQueued?: (narrationId: string) => void; }

export function TTSNarrationPanel({ timelineId, userId, playheadTime = 0, onQueued }: TTSNarrationPanelProps) {
  const [text, setText] = useState(""); const [style, setStyle] = useState<(typeof STYLES)[number]["id"]>("calm_narrator"); const [speed, setSpeed] = useState(1); const [pitch, setPitch] = useState(0); const [start, setStart] = useState(playheadTime); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const generate = async () => {
    if (!text.trim()) { setMessage("請先輸入旁白文字。"); return; }
    setPending(true); setMessage(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/narrations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, text, style, speed, pitch_semitones: pitch, timeline_start: start, language: "zh", caption_preset: "viral_yellow" }) });
      const data = await response.json() as { narration_id?: string; detail?: string };
      if (!response.ok || !data.narration_id) throw new Error(data.detail ?? "無法建立旁白任務");
      setMessage("旁白正在生成；完成後會自動加入音訊與字幕軌。"); onQueued?.(data.narration_id);
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "無法建立旁白任務"); } finally { setPending(false); }
  };
  return <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4"><div className="flex items-baseline justify-between"><div><h2 className="text-sm font-semibold text-zinc-100">AI 文字轉語音</h2><p className="mt-1 text-xs text-zinc-400">生成的旁白會自動建立音訊軌與逐詞字幕軌。</p></div><span className="text-[10px] text-zinc-500">{text.length}/4096</span></div><textarea value={text} maxLength={4096} onChange={(event) => setText(event.target.value)} placeholder="輸入想讓 AI 唸出的旁白…" className="mt-3 min-h-24 w-full rounded-lg border border-zinc-700 bg-zinc-900 p-2 text-sm text-zinc-100 outline-none focus:border-cyan-300" /><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">{STYLES.map((item) => <button key={item.id} type="button" onClick={() => setStyle(item.id)} className={`rounded-lg border p-2 text-left ${style === item.id ? "border-cyan-300 bg-cyan-300/10" : "border-zinc-700 bg-zinc-900"}`}><span className="block text-xs font-semibold text-zinc-100">{item.label}</span><span className="mt-1 block text-[10px] text-zinc-400">{item.detail}</span></button>)}</div><div className="mt-4 grid gap-3 sm:grid-cols-3"><label className="text-xs text-zinc-300">語速 <b className="text-white">{speed.toFixed(1)}x</b><input className="mt-1 w-full accent-cyan-300" type="range" min="0.7" max="1.3" step="0.1" value={speed} onChange={(event) => setSpeed(Number(event.target.value))} /></label><label className="text-xs text-zinc-300">語調 <b className="text-white">{pitch > 0 ? "+" : ""}{pitch} 半音</b><input className="mt-1 w-full accent-cyan-300" type="range" min="-6" max="6" step="1" value={pitch} onChange={(event) => setPitch(Number(event.target.value))} /></label><label className="text-xs text-zinc-300">插入時間 <b className="text-white">{start.toFixed(1)}s</b><input className="mt-1 w-full rounded bg-zinc-900 px-2 py-1 text-xs text-white" type="number" min="0" step="0.1" value={start} onChange={(event) => setStart(Math.max(0, Number(event.target.value)))} /></label></div><button type="button" disabled={pending} onClick={() => void generate()} className="mt-4 rounded bg-cyan-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "正在建立旁白…" : "生成旁白與同步字幕"}</button>{message && <p className={`mt-2 text-xs ${message.startsWith("旁白正在") ? "text-emerald-300" : "text-red-300"}`}>{message}</p>}</section>;
}

