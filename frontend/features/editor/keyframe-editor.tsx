"use client";

import { useEffect, useMemo, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { DEFAULT_BEZIER, createDefaultAnimation, type CubicBezier, type EasingKind, type TransformKeyframe } from "@/types/keyframes";
import type { TimelineClip } from "@/types/timeline";
import { KeyframeGraphEditor } from "@/features/editor/keyframe-graph-editor";
import { useKeyframeLivePreviewStore } from "@/features/editor/keyframe-live-preview-store";
import { evaluateTransformAt } from "@/types/keyframes";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const FIELDS = [
  ["x", "Position X", 0.01], ["y", "Position Y", 0.01], ["scale", "Scale", 0.01],
  ["rotation_degrees", "Rotation°", 1], ["z", "Camera Z", 0.01],
] as const;

interface KeyframeEditorProps {
  clip: TimelineClip;
  timelineId?: string;
}

function curvePath(curve: CubicBezier): string {
  return `M 0 100 C ${curve.x1 * 100} ${100 - curve.y1 * 100}, ${curve.x2 * 100} ${100 - curve.y2 * 100}, 100 0`;
}

export function KeyframeEditor({ clip, timelineId }: KeyframeEditorProps) {
  const animation = useTimelineStore((state) => state.clipAnimations[clip.id]);
  const setClipAnimation = useTimelineStore((state) => state.setClipAnimation);
  const setLiveTransform = useKeyframeLivePreviewStore((state) => state.setTransform);
  const clearLiveTransform = useKeyframeLivePreviewStore((state) => state.clear);
  const current = animation ?? createDefaultAnimation(clip.id, clip.source_end - clip.source_start);
  const [expanded, setExpanded] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isPersistable = Boolean(timelineId && !clip.id.startsWith("ai-clip-"));
  const curve = current.keyframes[0]?.cubic_bezier ?? DEFAULT_BEZIER;
  const summary = useMemo(() => `${current.keyframes.length} 個關鍵幀 · ${current.keyframes[0]?.easing ?? "ease-in-out"}`, [current]);

  const applyAnimation = (next: typeof current) => {
    setClipAnimation(next);
    const localTime = Math.max(0, useTimelineStore.getState().playheadTime - (clip.timeline_start ?? clip.source_start));
    setLiveTransform(clip.id, evaluateTransformAt(next, localTime));
  };
  useEffect(() => () => clearLiveTransform(clip.id), [clearLiveTransform, clip.id]);
  const replaceFrame = (index: number, change: Partial<TransformKeyframe>) => {
    const keyframes = current.keyframes.map((frame, frameIndex) => frameIndex === index ? { ...frame, ...change } : frame);
    applyAnimation({ ...current, keyframes });
  };
  const updateValue = (index: number, name: keyof TransformKeyframe["value"], value: number) => {
    replaceFrame(index, { value: { ...current.keyframes[index].value, [name]: value } });
  };
  const updateCurve = (key: keyof CubicBezier, value: number) => {
    const next = { ...curve, [key]: value };
    replaceFrame(0, { easing: "cubic-bezier", cubic_bezier: next });
  };
  const save = async () => {
    if (!timelineId || !isPersistable) return;
    const userId = window.localStorage.getItem("user_id");
    if (!userId) { setError("尚未取得登入使用者，無法儲存關鍵幀。"); return; }
    setIsSaving(true); setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/keyframes`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, animations: [current] }),
      });
      if (!response.ok) throw new Error("儲存關鍵幀失敗");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "儲存關鍵幀失敗");
    } finally { setIsSaving(false); }
  };

  return <section className="mt-4 border-t border-zinc-800 pt-3">
    <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-center justify-between rounded-md bg-zinc-800/70 px-3 py-2 text-left text-xs text-zinc-100">
      <span>關鍵幀與緩動</span><span className="text-zinc-400">{expanded ? "收合" : summary}</span>
    </button>
    {expanded && <div className="mt-3 space-y-3">
      <p className="text-[11px] leading-5 text-zinc-400">Position、Scale、Rotation 與 Camera Z 共用時間點；Z 會驅動 2.5D 前景／背景視差。</p>
      <KeyframeGraphEditor animation={current} duration={Math.max(.1, clip.source_end - clip.source_start)} onChange={applyAnimation} />
      {current.keyframes.map((frame, index) => <div key={`${frame.time}-${index}`} className="rounded-lg border border-zinc-700 p-2">
        <div className="mb-2 flex items-center justify-between gap-2"><span className="text-xs font-medium text-zinc-200">Keyframe {index + 1}</span>
          <label className="text-[11px] text-zinc-400">時間 <input className="ml-1 w-16 rounded bg-zinc-950 px-1 py-0.5 text-zinc-100" type="number" min="0" step="0.01" value={frame.time} onChange={(event) => replaceFrame(index, { time: Number(event.target.value) })} /></label>
        </div>
        <div className="grid grid-cols-2 gap-2">{FIELDS.map(([name, label, step]) => <label key={name} className="text-[10px] text-zinc-500">{label}
          <input className="mt-0.5 w-full rounded bg-zinc-950 px-1.5 py-1 text-xs text-zinc-100" type="number" step={step} value={frame.value[name]} onChange={(event) => updateValue(index, name, Number(event.target.value))} />
        </label>)}</div>
        {index < current.keyframes.length - 1 && <label className="mt-2 block text-[10px] text-zinc-500">進入下一幀的緩動
          <select className="mt-0.5 w-full rounded bg-zinc-950 px-1.5 py-1 text-xs text-zinc-100" value={frame.easing} onChange={(event) => replaceFrame(index, { easing: event.target.value as EasingKind, cubic_bezier: event.target.value === "cubic-bezier" ? curve : null })}>
            <option value="linear">Linear</option><option value="ease-in-out">Ease in out</option><option value="cubic-bezier">Custom cubic Bézier</option>
          </select>
        </label>}
      </div>)}
      {current.keyframes[0]?.easing === "cubic-bezier" && <div className="rounded-lg border border-violet-500/40 bg-violet-500/5 p-2">
        <svg viewBox="0 0 100 100" className="h-28 w-full rounded bg-zinc-950" aria-label="Cubic Bézier curve">
          <path d="M 0 100 L 100 0" stroke="#3f3f46" strokeDasharray="3 3" fill="none" /><path d={curvePath(curve)} stroke="#a78bfa" strokeWidth="2" fill="none" />
          <line x1="0" y1="100" x2={curve.x1 * 100} y2={100 - curve.y1 * 100} stroke="#71717a" /><line x1="100" y1="0" x2={curve.x2 * 100} y2={100 - curve.y2 * 100} stroke="#71717a" />
          {([ ["x1", "y1"], ["x2", "y2"] ] as const).map(([x, y]) => <circle key={x} cx={curve[x] * 100} cy={100 - curve[y] * 100} r="4" fill="#c4b5fd" className="cursor-grab" onPointerDown={(event) => {
            const handle = event.currentTarget; const svg = handle.ownerSVGElement; if (!svg) return; handle.setPointerCapture(event.pointerId);
            const move = (moveEvent: PointerEvent) => { const rect = svg.getBoundingClientRect(); const next = { ...curve, [x]: Math.max(0, Math.min(1, (moveEvent.clientX - rect.left) / rect.width)), [y]: Math.max(-2, Math.min(2, 1 - (moveEvent.clientY - rect.top) / rect.height)) }; replaceFrame(0, { easing: "cubic-bezier", cubic_bezier: next }); };
            const cleanup = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", cleanup); handle.removeEventListener("pointercancel", cleanup); };
            handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", cleanup); handle.addEventListener("pointercancel", cleanup);
          }} />)}
        </svg>
        <div className="mt-2 grid grid-cols-4 gap-1">{(["x1", "y1", "x2", "y2"] as const).map((key) => <label key={key} className="text-[10px] text-zinc-500">{key}<input className="mt-0.5 w-full rounded bg-zinc-950 px-1 py-0.5 text-zinc-100" type="number" step="0.01" value={curve[key]} onChange={(event) => updateCurve(key, Number(event.target.value))} /></label>)}</div>
      </div>}
      <button type="button" onClick={save} disabled={!isPersistable || isSaving} className="w-full rounded bg-violet-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">{isSaving ? "儲存中…" : "儲存關鍵幀"}</button>
      {!isPersistable && <p className="text-[10px] text-amber-300">請使用已儲存且有 UUID 的 Clip 後再提交到後端。</p>}{error && <p className="text-[10px] text-red-300">{error}</p>}
    </div>}
  </section>;
}
