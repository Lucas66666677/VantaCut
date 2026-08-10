"use client";

import type { ClipLayout } from "@/types/timeline";

interface CutReviewPopoverProps {
  clip: ClipLayout;
  onConfirmCut: () => void;
  onKeep: () => void;
  onClose: () => void;
}

export function CutReviewPopover({ clip, onConfirmCut, onKeep, onClose }: CutReviewPopoverProps) {
  return (
    <div className="absolute z-30 w-72 rounded-xl border border-zinc-700 bg-zinc-950 p-3 shadow-2xl" role="dialog" aria-label="AI 裁切建議">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-red-300">AI 建議裁切</span>
        <button onClick={onClose} className="text-zinc-500 hover:text-white" aria-label="關閉">×</button>
      </div>
      <p className="text-sm leading-5 text-zinc-200">{clip.reason}</p>
      <p className="mt-2 text-xs text-zinc-400">信心分數：{clip.confidence_score}%</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button onClick={onKeep} className="rounded-md bg-blue-600 px-2 py-1.5 text-xs font-semibold text-white hover:bg-blue-500">保留</button>
        <button onClick={onConfirmCut} className="rounded-md bg-red-600 px-2 py-1.5 text-xs font-semibold text-white hover:bg-red-500">確認裁切</button>
      </div>
    </div>
  );
}

