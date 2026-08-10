"use client";

import { useState } from "react";

import { useProjectStatus } from "@/features/project-status/use-project-status";
import { useBeatSyncMontage } from "@/features/one-click/use-beat-sync-montage";

interface BeatSyncMontagePanelProps {
  projectId: string;
  userId: string;
  bgmAssetId: string | null;
  mediaAssetIds: string[];
}

/** Reusable panel: the host media picker supplies the selected 10–30 asset IDs. */
export function BeatSyncMontagePanel({ projectId, userId, bgmAssetId, mediaAssetIds }: BeatSyncMontagePanelProps) {
  const { error, generate, isGenerating } = useBeatSyncMontage(projectId, userId);
  const projectStatus = useProjectStatus(projectId);
  const [started, setStarted] = useState(false);
  const [aspectRatio, setAspectRatio] = useState<"9:16" | "16:9">("9:16");

  const isValid = Boolean(bgmAssetId) && mediaAssetIds.length >= 10 && mediaAssetIds.length <= 30;
  const progress = started ? projectStatus?.progress ?? 0 : 0;

  const generateMontage = async () => {
    if (!bgmAssetId) return;
    await generate({ bgmAssetId, mediaAssetIds, aspectRatio, autoRender: true });
    setStarted(true);
  };

  return (
    <section className="rounded-xl border border-fuchsia-500/30 bg-slate-950 p-4 text-slate-100 shadow-lg">
      <div className="mb-3">
        <h2 className="text-base font-semibold">AI 一鍵卡點</h2>
        <p className="text-xs text-slate-400">依 BGM 強拍自動挑選高動態片段，並在高潮加入白閃、黑閃或震動。</p>
      </div>
      <div className="mb-3 flex items-center justify-between text-xs text-slate-300">
        <span>{mediaAssetIds.length}/30 個素材</span>
        <select aria-label="輸出比例" value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value as "9:16" | "16:9")} className="rounded bg-slate-800 px-2 py-1">
          <option value="9:16">9:16 直式</option>
          <option value="16:9">16:9 橫式</option>
        </select>
      </div>
      <button type="button" onClick={() => void generateMontage()} disabled={!isValid || isGenerating} className="w-full rounded bg-fuchsia-600 px-4 py-2 text-sm font-semibold transition hover:bg-fuchsia-500 disabled:cursor-not-allowed disabled:opacity-50">
        {isGenerating ? "正在建立卡點任務…" : "一鍵生成 Montage"}
      </button>
      {!isValid && <p className="mt-2 text-xs text-amber-300">請先選擇一首 BGM 與 10–30 個照片或影片素材。</p>}
      {started && <div className="mt-4"><div className="mb-1 flex justify-between text-xs text-slate-400"><span>{projectStatus?.message ?? "等待工作節點"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded bg-slate-800"><div className="h-full bg-fuchsia-500 transition-all" style={{ width: `${progress}%` }} /></div></div>}
      {error && <p role="alert" className="mt-3 text-xs text-rose-400">{error}</p>}
    </section>
  );
}
