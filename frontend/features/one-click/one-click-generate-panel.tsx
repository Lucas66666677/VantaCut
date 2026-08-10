"use client";

import { useState } from "react";

import { useOneClickStore } from "@/features/one-click/one-click-store";
import { useOneClickGenerate } from "@/features/one-click/use-one-click-generate";
import { useProjectStatus } from "@/features/project-status/use-project-status";

interface OneClickGeneratePanelProps {
  projectId: string;
  userId: string;
  mediaAssetIds: string[];
  bgmAssetId?: string;
}

export function OneClickGeneratePanel({ projectId, userId, mediaAssetIds, bgmAssetId }: OneClickGeneratePanelProps) {
  const templates = useOneClickStore((state) => state.templates);
  const selectedTemplateId = useOneClickStore((state) => state.selectedTemplateId);
  const selectTemplate = useOneClickStore((state) => state.selectTemplate);
  const isGenerating = useOneClickStore((state) => state.isGenerating);
  const error = useOneClickStore((state) => state.error);
  const { generate } = useOneClickGenerate(projectId, userId);
  const status = useProjectStatus(projectId);
  const [started, setStarted] = useState(false);
  const template = templates.find((item) => item.id === selectedTemplateId);
  const progress = started ? status?.progress ?? 0 : 0;

  const handleGenerate = async () => {
    if (!selectedTemplateId) return;
    await generate({ templateId: selectedTemplateId, mediaAssetIds, bgmAssetId, autoRender: true });
    setStarted(true);
  };

  return (
    <section className="rounded-xl border border-slate-700 bg-slate-950 p-4 text-slate-100 shadow-lg">
      <div className="mb-3"><h2 className="text-base font-semibold">AI 一鍵成片</h2><p className="text-xs text-slate-400">AI 依清晰度、人臉與動態挑片，並對齊模板節拍。</p></div>
      <select aria-label="選擇一鍵成片模板" value={selectedTemplateId ?? ""} onChange={(event) => selectTemplate(event.target.value)} className="mb-3 w-full rounded bg-slate-800 px-3 py-2 text-sm">
        {templates.map((item) => <option key={item.id} value={item.id}>{item.name}・{item.slot_count} 個鏡頭槽</option>)}
      </select>
      {template && <p className="mb-3 text-xs text-slate-400">{template.bgm.search_keywords?.join(" · ") ?? "使用自選 BGM"} / {template.bgm.target_bpm ?? "—"} BPM</p>}
      <button type="button" onClick={() => void handleGenerate()} disabled={!selectedTemplateId || !mediaAssetIds.length || isGenerating} className="w-full rounded bg-violet-600 px-4 py-2 text-sm font-semibold transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50">
        {isGenerating ? "正在建立任務…" : "一鍵生成並渲染"}
      </button>
      {started && <div className="mt-4"><div className="mb-1 flex justify-between text-xs text-slate-400"><span>{status?.message ?? "等待工作節點"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded bg-slate-800"><div className="h-full bg-violet-500 transition-all" style={{ width: `${progress}%` }} /></div></div>}
      {error && <p role="alert" className="mt-3 text-xs text-rose-400">{error}</p>}
    </section>
  );
}
