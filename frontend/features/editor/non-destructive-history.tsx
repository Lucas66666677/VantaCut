"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TimelineClip } from "@/types/timeline";
import type { ClipTransformAnimation } from "@/types/keyframes";
import type { ClipSpeedCurve } from "@/types/speed-curves";

export interface SandboxSnapshot { clips: TimelineClip[]; clipAnimations: Record<string, ClipTransformAnimation>; speedCurves: Record<string, ClipSpeedCurve>; }
export interface SandboxHistoryNode extends SandboxSnapshot { id: string; parentId: string | null; label: string; createdAt: number; }

const clone = (value: SandboxSnapshot): SandboxSnapshot => structuredClone(value);
const fingerprint = (value: SandboxSnapshot) => JSON.stringify(value);
const nodeId = () => `sandbox-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

function labelFor(previous: SandboxSnapshot, next: SandboxSnapshot) {
  if (previous.clips.length !== next.clips.length) return next.clips.length > previous.clips.length ? "新增軌道或片段" : "移除片段";
  if (JSON.stringify(previous.speedCurves) !== JSON.stringify(next.speedCurves)) return "調整變速曲線";
  if (JSON.stringify(previous.clipAnimations) !== JSON.stringify(next.clipAnimations)) return "調整關鍵幀動畫";
  if (previous.clips.some((clip, index) => clip.audio_effects.join("|") !== next.clips[index]?.audio_effects.join("|"))) return "調整 AI 音訊效果";
  return "編輯沙盒變更";
}

export function useTimelineSandboxHistory(snapshot: SandboxSnapshot, restore: (snapshot: SandboxSnapshot) => void) {
  const [nodes, setNodes] = useState<Record<string, SandboxHistoryNode>>({});
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<{ before: string; after: string } | null>(null);
  const lastRef = useRef<SandboxSnapshot | null>(null); const restoringFingerprint = useRef<string | null>(null);
  const currentFingerprint = fingerprint(snapshot);

  useEffect(() => {
    if (!snapshot.clips.length) return;
    if (restoringFingerprint.current === currentFingerprint) { lastRef.current = clone(snapshot); restoringFingerprint.current = null; return; }
    if (!lastRef.current) {
      const root: SandboxHistoryNode = { id: nodeId(), parentId: null, label: "原始時間軸", createdAt: Date.now(), ...clone(snapshot) };
      lastRef.current = clone(snapshot); setNodes({ [root.id]: root }); setCurrentId(root.id); return;
    }
    if (fingerprint(lastRef.current) === currentFingerprint) return;
    const previous = clone(lastRef.current); const next: SandboxHistoryNode = { id: nodeId(), parentId: currentId, label: labelFor(previous, snapshot), createdAt: Date.now(), ...clone(snapshot) };
    lastRef.current = clone(snapshot); setNodes((items) => ({ ...items, [next.id]: next })); setCurrentId(next.id); setComparison(null);
  }, [currentFingerprint, currentId, snapshot]);

  const checkout = useCallback((id: string) => {
    const node = nodes[id]; if (!node) return;
    const next = clone(node); restoringFingerprint.current = fingerprint(next); restore(next); setCurrentId(id); setComparison(null);
  }, [nodes, restore]);
  const undo = useCallback(() => { const parent = currentId ? nodes[currentId]?.parentId : null; if (parent) checkout(parent); }, [checkout, currentId, nodes]);
  const children = useMemo(() => Object.values(nodes).filter((node) => node.parentId === currentId).sort((left, right) => right.createdAt - left.createdAt), [currentId, nodes]);
  const redo = useCallback((childId?: string) => { const next = childId ?? children[0]?.id; if (next) checkout(next); }, [checkout, children]);
  const ordered = useMemo(() => Object.values(nodes).sort((left, right) => left.createdAt - right.createdAt), [nodes]);
  return { nodes: ordered, currentId, comparison, setComparison, checkout, undo, redo, canUndo: Boolean(currentId && nodes[currentId]?.parentId), canRedo: children.length > 0 };
}

export function VersionComparisonDialog({ comparison, nodes, previewUrl, onClose, onChoose }: { comparison: { before: string; after: string } | null; nodes: SandboxHistoryNode[]; previewUrl?: string; onClose: () => void; onChoose: (id: string) => void }) {
  if (!comparison) return null;
  const before = nodes.find((node) => node.id === comparison.before); const after = nodes.find((node) => node.id === comparison.after);
  if (!before || !after) return null;
  const pane = (node: SandboxHistoryNode, title: string) => <article className="rounded-xl border border-zinc-700 bg-zinc-950 p-3"><div className="mb-2 flex items-center justify-between"><b className="text-sm text-zinc-100">{title}</b><span className="text-[10px] text-zinc-500">{node.label}</span></div>{previewUrl ? <video muted controls preload="metadata" src={previewUrl} className="aspect-video w-full rounded bg-black" /> : <div className="grid aspect-video place-items-center rounded bg-gradient-to-br from-zinc-800 to-zinc-950 text-center text-xs text-zinc-500">此版本的 Proxy 預覽會在此載入<br />{node.clips.length} 個片段 · {Object.keys(node.speedCurves).length} 條變速曲線</div>}<button type="button" onClick={() => onChoose(node.id)} className="mt-3 rounded border border-zinc-600 px-2 py-1 text-xs text-zinc-200">採用這個版本</button></article>;
  return <div role="dialog" aria-modal="true" className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-4"><section className="w-full max-w-4xl rounded-2xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl"><div className="mb-4 flex items-center justify-between"><div><h2 className="font-semibold text-zinc-100">版本比較</h2><p className="text-xs text-zinc-400">兩個版本共用原始素材；採用其中一個只會切換 Filter Metadata。</p></div><button type="button" onClick={onClose} className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-200">關閉</button></div><div className="grid gap-4 md:grid-cols-2">{pane(before, "修改前")}{pane(after, "修改後")}</div></section></div>;
}
