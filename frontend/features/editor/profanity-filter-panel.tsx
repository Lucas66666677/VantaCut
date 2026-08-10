"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type SfxStyle = "beep" | "chicken" | "coin";
type EmojiStyle = "angry" | "duck";

interface ProfanityFilterPanelProps {
  timelineId: string;
  userId: string;
  onQueued?: (taskId: string) => void;
}

const SFX: Array<{ id: SfxStyle; label: string; description: string }> = [
  { id: "beep", label: "經典嗶聲", description: "最清楚、最保守的消音" },
  { id: "chicken", label: "尖叫雞", description: "搞笑卡通感" },
  { id: "coin", label: "金幣聲", description: "遊戲實況感" },
];

export function ProfanityFilterPanel({ timelineId, userId, onQueued }: ProfanityFilterPanelProps) {
  const [sfxStyle, setSfxStyle] = useState<SfxStyle>("beep");
  const [emojiStyle, setEmojiStyle] = useState<EmojiStyle>("angry");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const apply = async () => {
    setPending(true); setMessage(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/profanity-filter`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, sfx_style: sfxStyle, emoji_style: emojiStyle }),
      });
      const body = await response.json() as { task_id?: string; detail?: string };
      if (!response.ok || !body.task_id) throw new Error(body.detail ?? "無法建立敏感詞過濾任務");
      onQueued?.(body.task_id); setMessage("正在以 ASR 字級時間戳偵測敏感詞與追蹤嘴部…");
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "敏感詞過濾失敗"); }
    finally { setPending(false); }
  };

  return <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4 text-zinc-100">
    <h2 className="text-sm font-semibold">AI 髒話消音</h2><p className="mt-1 text-xs text-zinc-400">依 ASR 字級時間戳靜音，加入趣味音效並以 Face Mesh 對準嘴巴遮擋。</p>
    <div className="mt-3 grid grid-cols-3 gap-2">{SFX.map((item) => <button key={item.id} type="button" onClick={() => setSfxStyle(item.id)} className={`rounded-lg border p-2 text-left text-xs ${sfxStyle === item.id ? "border-fuchsia-400 bg-fuchsia-400/10" : "border-zinc-700"}`}><span className="block font-medium">{item.label}</span><span className="mt-1 block text-[10px] text-zinc-400">{item.description}</span></button>)}</div>
    <div className="mt-3 flex items-center gap-3 text-xs"><span className="text-zinc-400">嘴部 Emoji</span><button type="button" onClick={() => setEmojiStyle("angry")} className={`rounded px-2 py-1 ${emojiStyle === "angry" ? "bg-rose-500 text-zinc-950" : "bg-zinc-800"}`}>🤬 生氣</button><button type="button" onClick={() => setEmojiStyle("duck")} className={`rounded px-2 py-1 ${emojiStyle === "duck" ? "bg-amber-300 text-zinc-950" : "bg-zinc-800"}`}>🦆 鴨子</button></div>
    <button type="button" onClick={() => void apply()} disabled={pending} className="mt-4 w-full rounded bg-fuchsia-500 px-3 py-2 text-sm font-bold text-zinc-950 disabled:opacity-50">{pending ? "分析與追蹤中…" : "一鍵消音並趣味遮擋"}</button>
    {message && <p className={`mt-2 text-xs ${message.startsWith("正在") ? "text-emerald-300" : "text-rose-300"}`}>{message}</p>}
  </section>;
}
