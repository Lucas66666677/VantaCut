"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface VerticalDualLayoutPanelProps { timelineId: string; userId: string; sourceAssetId?: string; }

export function VerticalDualLayoutPanel({ timelineId, userId, sourceAssetId }: VerticalDualLayoutPanelProps) {
  const [topRatio, setTopRatio] = useState(43); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const create = async () => {
    if (!sourceAssetId) { setMessage("時間軸中找不到可用的主影片素材。"); return; }
    setPending(true); setMessage(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/vertical-dual-layout`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, source_asset_id: sourceAssetId, top_ratio: topRatio / 100, max_samples: 48 }) });
      const payload = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "無法建立雙畫面分析任務");
      setMessage("AI 正在找尋人臉與遊戲焦點；完成後導出會自動轉為直式雙畫面。");
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "無法建立雙畫面分析任務"); } finally { setPending(false); }
  };
  return <section className="rounded-xl border border-cyan-400/30 bg-zinc-950 p-4 text-zinc-100"><h2 className="text-sm font-semibold">一鍵直式雙畫面</h2><p className="mt-1 text-xs text-zinc-400">上半部保留鏡頭人臉，下半部保留遊戲／Reaction 主畫面；空白處自動補動態模糊背景。</p><label className="mt-3 block text-xs text-zinc-300">人臉畫面高度 <b className="text-white">{topRatio}%</b><input type="range" min="30" max="60" value={topRatio} onChange={(event) => setTopRatio(Number(event.target.value))} className="mt-2 w-full accent-cyan-300" /></label><button type="button" disabled={pending || !sourceAssetId} onClick={() => void create()} className="mt-3 rounded bg-cyan-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "正在定位畫面…" : "生成直式雙畫面"}</button>{message && <p className={`mt-2 text-xs ${message.startsWith("AI 正在") ? "text-emerald-300" : "text-red-300"}`}>{message}</p>}</section>;
}
