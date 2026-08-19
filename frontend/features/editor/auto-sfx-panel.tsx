"use client";

import { useState } from "react";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

interface AudioAssetOption { id: string; filename: string; }
interface AutoSfxPanelProps { timelineId: string; userId: string; audioAssets: AudioAssetOption[]; onConfigured?: (eventCount: number) => void; }
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Maps a user-owned SFX pack to caption/transition events; no unlicensed audio is bundled. */
export function AutoSfxPanel({ timelineId, userId, audioAssets, onConfigured }: AutoSfxPanelProps) {
  const [pop, setPop] = useState(""); const [whoosh, setWhoosh] = useState(""); const [impact, setImpact] = useState(""); const [bgm, setBgm] = useState("");
  const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null); const [eventCount, setEventCount] = useState<number | null>(null);
  const picker = (label: string, value: string, setValue: (value: string) => void) => <label className="block text-xs text-zinc-300">{label}<select value={value} onChange={(event) => setValue(event.target.value)} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs"><option value="">不套用</option>{audioAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}</select></label>;
  const apply = async () => {
    setPending(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/auto-sfx`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pop_asset_id: pop || null, whoosh_asset_id: whoosh || null, impact_asset_id: impact || null, bgm_asset_id: bgm || null, bgm_volume: .16, ducking_enabled: true }) });
      const payload = await response.json() as { detail?: string; event_count?: number };
      if (!response.ok || payload.event_count === undefined) throw new Error(payload.detail ?? "無法建立 Auto-SFX 軌");
      setEventCount(payload.event_count); onConfigured?.(payload.event_count);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法建立 Auto-SFX 軌"); }
    finally { setPending(false); }
  };
  return <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4"><h2 className="text-sm font-semibold text-zinc-100">Auto-SFX 音效設計</h2><p className="mt-1 text-xs text-zinc-400">動態字幕 → Pop；縮放／故障轉場 → Whoosh；爆炸字 → Impact。說話時自動壓低 BGM。</p><div className="mt-3 grid gap-2 sm:grid-cols-2">{picker("Pop／啵啵聲", pop, setPop)}{picker("Whoosh／呼嘯聲", whoosh, setWhoosh)}{picker("Impact／衝擊聲", impact, setImpact)}{picker("BGM（可選）", bgm, setBgm)}</div><button type="button" onClick={() => void apply()} disabled={pending} className="mt-3 rounded bg-amber-400 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "正在建立音效軌…" : "自動加入音效與 BGM 閃避"}</button>{eventCount !== null && <p className="mt-2 text-xs text-emerald-300">已加入 {eventCount} 個對齊音效事件。</p>}{error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}</section>;
}
