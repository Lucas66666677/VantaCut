"use client";

import { useState } from "react";

import { BilingualCaptionCanvas, type BilingualCaptionCue } from "@/features/captions/bilingual-caption-canvas";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const LANGUAGES = [{ id: "en", label: "English" }, { id: "ja", label: "日本語" }, { id: "es", label: "Español" }];

interface BilingualCaptionPanelProps {
  timelineId: string;
  userId: string;
  cues: BilingualCaptionCue[];
  currentTimeMs: number;
  previewWidth?: number;
  previewHeight?: number;
}

export function BilingualCaptionPanel({ timelineId, cues, currentTimeMs, previewWidth = 270, previewHeight = 480 }: BilingualCaptionPanelProps) {
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setPending(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/generate-bilingual-subtitles`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_language: targetLanguage }),
      });
      const body = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "無法建立雙語字幕任務");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "雙語字幕生成失敗");
    } finally { setPending(false); }
  };

  const download = async (format: "srt" | "vtt", track: "bilingual" | "source" | "target") => {
    const url = new URL(`${API_URL}/api/v1/timelines/${timelineId}/bilingual-subtitles/export`);
    url.searchParams.set("format", format); url.searchParams.set("track", track);
    const response = await authenticatedFetch(url.toString());
    if (!response.ok) { setError("無法下載雙語字幕"); return; }
    const objectUrl = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a"); anchor.href = objectUrl; anchor.download = `${timelineId}-${track}.${format}`; anchor.click(); URL.revokeObjectURL(objectUrl);
  };

  return <section className="rounded-xl border border-sky-700/60 bg-slate-950 p-4 text-slate-100">
    <h2 className="text-sm font-semibold">AI 雙語字幕</h2>
    <p className="mt-1 text-xs text-slate-400">主字幕保留動態花字，翻譯以安定的小字緊貼在下方。</p>
    <div className="relative mx-auto mt-4 aspect-[9/16] max-w-[270px] overflow-hidden rounded-lg bg-gradient-to-b from-slate-700 to-slate-950">
      <BilingualCaptionCanvas cues={cues} currentTimeMs={currentTimeMs} width={previewWidth} height={previewHeight} />
    </div>
    <div className="mt-3 flex gap-2"><select aria-label="翻譯目標語言" value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)} className="flex-1 rounded bg-slate-800 px-3 py-2 text-sm">{LANGUAGES.map((language) => <option key={language.id} value={language.id}>{language.label}</option>)}</select><button type="button" onClick={() => void generate()} disabled={pending || !cues.length} className="rounded bg-sky-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">{pending ? "翻譯中…" : "一鍵生成"}</button></div>
    <div className="mt-3 grid grid-cols-3 gap-2 text-xs"><button type="button" onClick={() => void download("srt", "bilingual")} className="rounded border border-slate-700 py-2 hover:bg-slate-800">雙語 SRT</button><button type="button" onClick={() => void download("vtt", "source")} className="rounded border border-slate-700 py-2 hover:bg-slate-800">母語 VTT</button><button type="button" onClick={() => void download("vtt", "target")} className="rounded border border-slate-700 py-2 hover:bg-slate-800">外語 VTT</button></div>
    {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
  </section>;
}
