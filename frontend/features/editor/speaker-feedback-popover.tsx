"use client";

import type { ClipLayout } from "@/types/timeline";

interface SpeakerFeedbackPopoverProps {
  clip: ClipLayout;
  onClose: () => void;
}

const METRIC_LABELS: Record<string, string> = {
  eye_contact: "眼神接觸",
  head_alignment: "頭部對鏡",
  head_depth_alignment: "臉部正向度",
  posture_openness: "姿態開放度",
  gesture_amplitude: "手勢幅度",
  gesture_motion: "手勢動態",
  expression_energy: "表情動態",
  blink_rate_per_min: "眨眼／閉眼頻率",
  body_rigidity_proxy: "肢體僵硬度訊號",
};

export function SpeakerFeedbackPopover({ clip, onClose }: SpeakerFeedbackPopoverProps) {
  const state = clip.speaker_state;
  const hints = [...(clip.review_flags ?? []), ...(clip.creator_hints ?? [])];
  if (!state && !hints.length) return null;

  return (
    <div className="absolute z-30 w-80 rounded-xl border border-amber-500/50 bg-zinc-950 p-3 shadow-2xl" role="dialog" aria-label="講者呈現建議">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-amber-200">講者呈現建議</span>
        <button onClick={onClose} className="text-zinc-500 hover:text-white" aria-label="關閉">×</button>
      </div>
      {state?.assessment_status === "insufficient_visual_evidence" ? (
        <p className="rounded-md bg-zinc-900 p-2 text-xs leading-5 text-zinc-300">此段未能穩定辨識講者臉部，因此未產生可信的自信度或流暢度分數。</p>
      ) : state && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="rounded-md bg-zinc-900 p-2 text-zinc-300">自信呈現 <strong className="text-amber-200">{state.confidence_score}</strong>/100</div>
          <div className="rounded-md bg-zinc-900 p-2 text-zinc-300">流暢度 <strong className="text-amber-200">{state.fluency_score}</strong>/100</div>
        </div>
      )}
      {state?.metrics && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-zinc-400">
          {Object.entries(state.metrics).filter(([key]) => key in METRIC_LABELS).map(([key, value]) => (
            <span key={key}>{METRIC_LABELS[key]}：{value}/100</span>
          ))}
        </div>
      )}
      <ul className="mt-3 space-y-1.5 text-xs leading-5 text-zinc-200">
        {hints.map((hint) => <li key={hint}>• {hint}</li>)}
      </ul>
      <p className="mt-3 text-[10px] leading-4 text-zinc-500">此為畫面呈現建議，請由創作者自行確認；不會自動裁切片段。</p>
    </div>
  );
}
