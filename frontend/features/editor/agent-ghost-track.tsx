"use client";

import { useMemo } from "react";

import { buildTrackLayouts } from "@/features/editor/timeline-layout";
import type { AgentProposal } from "@/features/editor/agentic-proposal-store";
import type { TimelineClip } from "@/types/timeline";

export function AgentGhostTrack({ proposal, baseline, zoom }: { proposal: AgentProposal; baseline: TimelineClip[]; zoom: number }) {
  const changedIds = useMemo(() => new Set(proposal.snapshot.clips.filter((clip) => {
    const before = baseline.find((item) => item.id === clip.id);
    return !before || before.source_start !== clip.source_start || before.source_end !== clip.source_end || before.lut_key !== clip.lut_key || before.audio_gain_db !== clip.audio_gain_db;
  }).map((clip) => clip.id)), [baseline, proposal.snapshot.clips]);
  const layouts = useMemo(() => buildTrackLayouts(proposal.snapshot.clips), [proposal.snapshot.clips]);
  const clips = [...layouts.values()].flat().filter((clip) => changedIds.has(clip.id));
  if (!clips.length) return null;
  return <div className="relative h-14 rounded-lg border border-dashed border-fuchsia-300/70 bg-fuchsia-500/10"><span className="absolute left-2 top-2 rounded bg-fuchsia-950 px-1.5 py-0.5 text-[10px] text-fuchsia-100">AI Ghost Track · 尚未採納</span>{clips.map((clip) => <div key={clip.id} className="absolute top-7 h-6 rounded border border-dashed border-fuchsia-200 bg-fuchsia-400/25 px-2 text-[10px] leading-6 text-fuchsia-50" style={{ left: clip.displayStart * zoom, width: Math.max(14, (clip.displayEnd - clip.displayStart) * zoom) }}>{clip.track === "b_roll" ? "B-Roll" : clip.track === "audio_overlay" ? "BGM 提案" : clip.lut_key ? "LUT" : "AI 修剪"}</div>)}</div>;
}
