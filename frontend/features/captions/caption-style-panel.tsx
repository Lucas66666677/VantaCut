"use client";

import { useState } from "react";

import { type CaptionVisualStyle, KineticCaptionCanvas, type KineticCaptionCue } from "@/features/captions/kinetic-caption-canvas";
import { DomWebglTextStage } from "@/features/captions/dom-webgl-text-stage";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PRESETS: Array<{ id: CaptionVisualStyle; label: string; description: string }> = [
  { id: "viral_yellow", label: "黃字黑邊蹦字", description: "粗體黃字、黑色粗描邊、逐字彈跳" },
  { id: "karaoke_pop", label: "卡拉 OK 高亮", description: "依字級時間戳高亮，重點字帶彈跳" },
  { id: "clean_white", label: "乾淨白字", description: "白字黑邊，適合資訊密度高的教學" },
];

interface CaptionStylePanelProps {
  timelineId: string;
  userId: string;
  cues: KineticCaptionCue[];
  currentTimeMs: number;
  previewWidth?: number;
  previewHeight?: number;
}

export function CaptionStylePanel({ timelineId, cues, currentTimeMs, previewWidth = 270, previewHeight = 480 }: CaptionStylePanelProps) {
  const [preset, setPreset] = useState<CaptionVisualStyle>("viral_yellow");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = async () => {
    setSaving(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/caption-style`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset, aspect_ratio: "9:16" }),
      });
      const payload = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "無法套用字幕樣式");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法套用字幕樣式");
    } finally { setSaving(false); }
  };

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4 text-zinc-100">
      <h2 className="text-sm font-semibold">逐字彈跳字幕</h2>
      <p className="mt-1 text-xs text-zinc-400">目前發音字會回彈放大；動詞與數字自動高亮，其他字淡化保留上下文。</p>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {PRESETS.map((item) => <button key={item.id} type="button" onClick={() => setPreset(item.id)} className={`rounded-lg border p-2 text-left text-xs ${preset === item.id ? "border-yellow-300 bg-yellow-300/15 text-yellow-100" : "border-zinc-700 text-zinc-300"}`}><span className="block font-semibold">{item.label}</span><span className="mt-1 block text-[10px] opacity-70">{item.description}</span></button>)}
      </div>
      <div className="relative mx-auto mt-4 aspect-[9/16] max-w-[270px] overflow-hidden rounded-lg bg-gradient-to-b from-zinc-700 to-zinc-950">
        <KineticCaptionCanvas cues={cues} currentTimeMs={currentTimeMs} width={previewWidth} height={previewHeight} stylePreset={preset} />
      </div>
      <div className="mt-3 overflow-auto rounded-lg border border-zinc-800 p-2"><DomWebglTextStage width={previewWidth} height={Math.min(220, previewHeight)} initialText={cues[0]?.text ?? "雙擊輸入文字"} /></div>
      <button type="button" onClick={() => void apply()} disabled={saving || !cues.length} className="mt-3 w-full rounded-md bg-yellow-400 px-3 py-2 text-sm font-bold text-zinc-950 disabled:opacity-50">
        {saving ? "正在產生 ASS…" : "套用到直式影片"}
      </button>
      {error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}
    </section>
  );
}
