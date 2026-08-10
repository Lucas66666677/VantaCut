"use client";

import { useEffect, useRef, useState } from "react";

import { useTimelineStore, type NudgeCommandInput } from "@/features/editor/timeline-store";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface NudgeResponse { commands: NudgeCommandInput[]; explanation: string; }

export function NudgeCommandBar({ timelineId, userId }: { timelineId?: string; userId?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [instruction, setInstruction] = useState("");
  const [pending, setPending] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clips = useTimelineStore((state) => state.clips);
  const selectedClipId = useTimelineStore((state) => state.selectedClipId);
  const applyNudgeCommands = useTimelineStore((state) => state.applyNudgeCommands);
  const targetClipIds = selectedClipId ? [selectedClipId] : clips.filter((clip) => clip.track === "main_video" && clip.reviewStatus !== "cut").map((clip) => clip.id);

  useEffect(() => {
    const focus = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); inputRef.current?.focus(); }
    };
    window.addEventListener("keydown", focus); return () => window.removeEventListener("keydown", focus);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4600); return () => window.clearTimeout(timer);
  }, [toast]);

  const submit = async () => {
    const value = instruction.trim();
    if (!value || !timelineId || !userId || !targetClipIds.length) return;
    setPending(true); setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/nudge`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, instruction: value, target_clip_ids: targetClipIds }),
      });
      const result = await response.json() as NudgeResponse & { detail?: string };
      if (!response.ok) throw new Error(result.detail ?? "無法理解這個微調指令");
      if (result.commands.length) applyNudgeCommands(result.commands);
      setToast(result.explanation); setInstruction("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法套用微調指令");
    } finally { setPending(false); }
  };

  return <div className="relative mt-4">
    <div className="flex items-center gap-2 rounded-2xl border border-violet-300/35 bg-zinc-950 p-2 shadow-[0_0_30px_rgba(139,92,246,.12)]">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-violet-400/15 text-sm text-violet-100">✦</span>
      <input ref={inputRef} value={instruction} onChange={(event) => setInstruction(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submit(); }} placeholder="例如：讓這段看起來更有活力一點" className="min-w-0 flex-1 bg-transparent px-1 text-sm text-zinc-100 outline-none placeholder:text-zinc-500" aria-label="AI 微調指令" />
      <span className="hidden rounded border border-zinc-700 px-1.5 py-0.5 text-[10px] text-zinc-500 sm:block">⌘ K</span>
      <button type="button" disabled={pending || !instruction.trim() || !timelineId || !userId || !targetClipIds.length} onClick={() => void submit()} className="rounded-xl bg-violet-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:cursor-not-allowed disabled:opacity-40">{pending ? "理解中…" : "微調"}</button>
    </div>
    <p className="mt-1 px-2 text-[11px] text-zinc-500">{selectedClipId ? "套用至目前選取片段" : "未選取片段：會套用至所有保留的主軌片段"} · 只建立可撤銷的非破壞性設定</p>
    {error && <p className="mt-1 px-2 text-xs text-red-300">{error}</p>}
    {toast && <div role="status" className="absolute bottom-[calc(100%+8px)] left-1/2 z-50 w-[min(94vw,520px)] -translate-x-1/2 rounded-xl border border-violet-300/35 bg-zinc-900 px-4 py-3 text-center text-sm text-violet-50 shadow-2xl animate-in fade-in slide-in-from-bottom-2">{toast}</div>}
  </div>;
}
