"use client";

import { useState } from "react";

import type { TimelineSticker } from "@/features/editor/sticker-canvas-overlay";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
interface StickerResponse { enabled: boolean; items: TimelineSticker[]; detail?: string; }

interface AIStickerPanelProps { timelineId: string; userId: string; onChanged?: (data: { enabled: boolean; items: TimelineSticker[] }) => void; }

export function AIStickerPanel({ timelineId, onChanged }: AIStickerPanelProps) {
  const [enabled, setEnabled] = useState(true); const [items, setItems] = useState<TimelineSticker[]>([]); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const applyResponse = (response: StickerResponse) => { setEnabled(response.enabled); setItems(response.items); onChanged?.({ enabled: response.enabled, items: response.items }); };
  const recommend = async () => {
    setPending(true); setMessage(null);
    try { const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/recommend-stickers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) }); const data = await response.json() as StickerResponse; if (!response.ok) throw new Error(data.detail ?? "無法推薦貼紙"); applyResponse(data); setMessage(`已建立 ${data.items.length} 個 AI 貼紙建議`); } catch (cause) { setMessage(cause instanceof Error ? cause.message : "無法推薦貼紙"); } finally { setPending(false); }
  };
  const toggle = async (next: boolean) => {
    setPending(true); setMessage(null);
    try { const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/ai-stickers/enabled`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: next }) }); const data = await response.json() as StickerResponse; if (!response.ok) throw new Error(data.detail ?? "無法更新貼紙開關"); applyResponse(data); } catch (cause) { setMessage(cause instanceof Error ? cause.message : "無法更新貼紙開關"); } finally { setPending(false); }
  };
  return <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4"><div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">AI 語意貼紙</h2><p className="mt-1 text-xs text-zinc-400">依字幕中的地點、名詞與情緒自動推薦，並可在畫布上微調。</p></div><label className="flex items-center gap-2 text-xs text-zinc-200"><input type="checkbox" checked={enabled} disabled={pending || items.length === 0} onChange={(event) => void toggle(event.target.checked)} className="accent-cyan-400" />顯示 AI 貼紙</label></div><button type="button" onClick={() => void recommend()} disabled={pending} className="mt-3 rounded bg-cyan-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "分析中…" : "一鍵套用 AI 貼紙"}</button>{items.length > 0 && <div className="mt-3 flex flex-wrap gap-1">{items.map((item) => <span key={item.id} className="rounded-full border border-zinc-700 px-2 py-1 text-[10px] text-zinc-300">{item.fallback_emoji} {item.label} · {item.trigger?.text}</span>)}</div>}{message && <p className={`mt-2 text-xs ${message.startsWith("已") ? "text-emerald-300" : "text-red-300"}`}>{message}</p>}</section>;
}

