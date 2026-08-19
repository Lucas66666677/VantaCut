"use client";

import { useState } from "react";

import type { BeautyPreviewSettings } from "@/features/editor/beauty-webgl-preview";
import { ClampNumberInput } from "@/features/editor/resilience-feedback";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const defaults = { enabled: true, skin_smoothing: 35, brightness: 8, contrast: 10, denoise: 30, sharpen: 25 };
type Settings = BeautyPreviewSettings & { denoise: number; sharpen: number };

export function BeautyEnhancementPanel({ timelineId, userId, onPreviewChange }: { timelineId: string; userId: string; onPreviewChange?: (settings: Settings) => void }) {
  const [settings, setSettings] = useState<Settings>(defaults); const [pending, setPending] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const update = (key: Exclude<keyof Settings, "enabled">, value: number) => { const next = { ...settings, [key]: value }; setSettings(next); onPreviewChange?.(next); };
  const save = async () => {
    setPending(true); setMessage(null);
    try { const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/beauty-enhancement`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }); const result = await response.json() as { detail?: string }; if (!response.ok) throw new Error(result.detail ?? "無法儲存美顏設定"); setMessage("已套用；最終導出會進行降噪與銳化。"); } catch (error) { setMessage(error instanceof Error ? error.message : "無法儲存美顏設定"); } finally { setPending(false); }
  };
  const labels: Array<[Exclude<keyof Settings, "enabled">, string, string]> = [["skin_smoothing", "輕度磨皮", "僅在 WebGL 預覽中以 Face Mesh 人臉區域套用"], ["brightness", "美白提亮", "提升肌膚亮度"], ["contrast", "對比增強", "改善灰霧感"], ["denoise", "低光降噪", "導出時使用 hqdn3d"], ["sharpen", "細節銳化", "導出時使用 unsharp"]];
  return <section className="rounded-xl border border-pink-400/25 bg-zinc-950 p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">一鍵美顏與畫質增強</h2><p className="mt-1 text-xs text-zinc-400">預覽以 MediaPipe Face Mesh 鎖定臉部；導出保留自然、可逆的低光修復。</p></div><button type="button" onClick={() => { const next = { ...settings, enabled: !settings.enabled }; setSettings(next); onPreviewChange?.(next); }} className={`rounded-full px-2 py-1 text-[10px] font-bold ${settings.enabled ? "bg-pink-400 text-zinc-950" : "bg-zinc-800 text-zinc-400"}`}>{settings.enabled ? "已開啟" : "已關閉"}</button></div><div className="mt-4 space-y-3">{labels.map(([key, label, detail]) => <label key={key} className="block text-xs text-zinc-200"><span className="flex items-center justify-between gap-2"><span>{label}<small className="ml-2 text-zinc-500">{detail}</small></span><span className="flex items-center gap-1"><ClampNumberInput value={settings[key]} min={key === "brightness" ? -100 : 0} max={100} onCommit={(value) => update(key, value)} ariaLabel={`${label} 百分比`} /><b>%</b></span></span><input disabled={!settings.enabled} type="range" min={key === "brightness" ? "-100" : "0"} max="100" value={settings[key]} onChange={(event) => update(key, Number(event.target.value))} className="mt-1 w-full accent-pink-300 disabled:opacity-35" /></label>)}</div><button type="button" disabled={pending} onClick={() => void save()} className="mt-4 rounded bg-pink-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "儲存中…" : "套用到最終導出"}</button>{message && <p className={`mt-2 text-xs ${message.startsWith("已") ? "text-emerald-300" : "text-red-300"}`}>{message}</p>}</section>;
}
