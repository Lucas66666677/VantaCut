"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Style = "gradient_line" | "liquid_fill" | "border_marquee";
type Platform = "tiktok" | "instagram_reels" | "youtube_shorts";

const labels: Record<Style, string> = { gradient_line: "漸層進度線", liquid_fill: "底部液體填充", border_marquee: "邊框跑馬燈" };

/** Safe-zone-aware preview and one-click persistence for retention-focused visual hooks. */
export function VisualHooksPanel({ timelineId, userId }: { timelineId: string; userId: string }) {
  const [style, setStyle] = useState<Style>("gradient_line"); const [platform, setPlatform] = useState<Platform>("tiktok"); const [enabled, setEnabled] = useState(false); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const save = async (nextEnabled: boolean) => {
    setPending(true); setMessage(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/visual-hooks`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, enabled: nextEnabled, style, platform, suspense_enabled: true }) });
      const body = await response.json() as { detail?: string; status?: string; suspense_text?: string };
      if (!response.ok) throw new Error(body.detail ?? "無法更新視覺鉤子");
      setEnabled(nextEnabled); setMessage(nextEnabled ? (body.suspense_text ? `已加入「${body.suspense_text}」懸念提示` : "已加入安全區進度條") : "已停用視覺鉤子");
    } catch (error) { setMessage(error instanceof Error ? error.message : "無法更新視覺鉤子"); } finally { setPending(false); }
  };
  const progressClass = style === "gradient_line" ? "h-1.5 bg-gradient-to-r from-fuchsia-400 via-sky-300 to-emerald-300" : style === "liquid_fill" ? "h-3 rounded-full bg-cyan-300 shadow-[0_0_16px_rgba(103,232,249,.9)]" : "hidden";
  return <section className="rounded-xl border border-cyan-400/25 bg-zinc-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">自動留存視覺鉤子</h2><p className="mt-1 text-xs text-zinc-400">進度暗示＋高能預告；文字與進度條避開平台 UI 熱區。</p></div><button type="button" role="switch" aria-checked={enabled} disabled={pending} onClick={() => void save(!enabled)} className={`relative h-7 w-12 rounded-full ${enabled ? "bg-cyan-400" : "bg-zinc-700"} disabled:opacity-50`}><span className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${enabled ? "left-6" : "left-1"}`} /></button></div><div className="mt-3 grid grid-cols-2 gap-2"><label className="text-xs text-zinc-300">樣式<select value={style} onChange={(event) => setStyle(event.target.value as Style)} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs">{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="text-xs text-zinc-300">平台安全區<select value={platform} onChange={(event) => setPlatform(event.target.value as Platform)} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs"><option value="tiktok">TikTok</option><option value="instagram_reels">Instagram Reels</option><option value="youtube_shorts">YouTube Shorts</option></select></label></div><div className="relative mx-auto mt-4 aspect-[9/16] w-32 overflow-hidden rounded-lg border border-zinc-700 bg-gradient-to-br from-zinc-700 to-zinc-950"><div className="absolute left-[5.5%] top-[12%] w-[67.5%] text-center text-[7px] font-black tracking-wide text-white"><span className="rounded bg-black/60 px-1.5 py-1">WAIT FOR IT · 15s</span></div>{style === "border_marquee" && <div className="absolute left-[5.5%] top-[12%] h-[58.5%] w-[67.5%] border-2 border-cyan-300/80" />}<div className={`absolute bottom-[27.6%] left-[5.5%] w-[67.5%] rounded-full bg-black/50 ${style === "liquid_fill" ? "h-3" : "h-1.5"}`}><div className={`${progressClass} w-[53%] rounded-full`} /></div><div className="absolute right-0 top-[24%] h-[49%] w-[20.5%] border border-dashed border-rose-300/50 bg-rose-500/10" /><div className="absolute bottom-0 left-0 h-[24.5%] w-[93%] border border-dashed border-rose-300/50 bg-rose-500/10" /></div><button type="button" disabled={pending} onClick={() => void save(true)} className="mt-3 rounded bg-cyan-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-45">{pending ? "正在套用…" : "一鍵添加進度條與高能預告"}</button>{message && <p className={`mt-2 text-xs ${message.includes("無法") ? "text-rose-300" : "text-emerald-300"}`}>{message}</p>}</section>;
}
