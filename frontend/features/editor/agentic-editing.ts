import type { SandboxSnapshot } from "@/features/editor/non-destructive-history";
import type { TimelineClip, TrackType } from "@/types/timeline";

export type AgentToolName = "trim_clip" | "insert_b_roll" | "adjust_audio_level" | "apply_lut" | "add_bgm";

export interface AgentToolCall {
  name: AgentToolName;
  arguments: Record<string, unknown>;
}

export interface AgentTimelineContext {
  schema: "agentic-timeline-context/v1";
  timeline: { duration_seconds: number; selected_clip_id: string | null };
  tracks: Array<{ type: TrackType; clips: Array<Record<string, unknown>> }>;
  /** Flattened intentionally: the LLM tool mock and providers can ground IDs without inferring nesting. */
  clips: Array<Record<string, unknown>>;
  approved_lut_keys: string[];
}

const rounded = (value: number) => Number(value.toFixed(3));
const durationOf = (clip: TimelineClip) => Math.max(0, clip.source_end - clip.source_start);

export function serialiseTimelineForAgent(
  clips: TimelineClip[], selectedClipId: string | null, approvedLutKeys: string[] = [],
): AgentTimelineContext {
  const compact = clips.slice(0, 160).map((clip) => ({
    clip_id: clip.id,
    source_asset_id: clip.source_asset_id ?? null,
    track: clip.track,
    timeline_start: rounded(clip.timeline_start ?? clip.source_start),
    source_start: rounded(clip.source_start),
    source_end: rounded(clip.source_end),
    duration_seconds: rounded(durationOf(clip)),
    enabled: clip.reviewStatus !== "cut",
    audio_enabled: clip.audio_enabled,
    gain_db: clip.audio_gain_db ?? 0,
    important_markers: [
      ...(clip.issue_types ?? []),
      ...(clip.review_flags ?? []),
      ...(clip.creator_hints ?? []),
    ].slice(0, 5),
  }));
  const tracks = (["main_video", "b_roll", "audio_overlay", "finance_overlay", "multicam_video"] as TrackType[])
    .map((type) => ({ type, clips: compact.filter((clip) => clip.track === type) }));
  const duration = clips.reduce((max, clip) => Math.max(max, (clip.timeline_start ?? clip.source_start) + durationOf(clip)), 0);
  return {
    schema: "agentic-timeline-context/v1",
    timeline: { duration_seconds: rounded(duration), selected_clip_id: selectedClipId },
    tracks,
    clips: compact,
    approved_lut_keys: approvedLutKeys.slice(0, 20),
  };
}

const numberArgument = (arguments_: Record<string, unknown>, key: string, fallback = 0) => {
  const value = Number(arguments_[key]); return Number.isFinite(value) ? value : fallback;
};

/** Pure reducer: a plan can be inspected or discarded without touching Zustand. */
export function applyAgentToolCalls(base: SandboxSnapshot, calls: AgentToolCall[]): SandboxSnapshot {
  const next = structuredClone(base) as SandboxSnapshot;
  const timelineDuration = next.clips.reduce((max, clip) => Math.max(max, (clip.timeline_start ?? clip.source_start) + durationOf(clip)), 0);
  for (const call of calls) {
    const args = call.arguments;
    if (call.name === "trim_clip") {
      const clipId = String(args.clip_id ?? ""); const clip = next.clips.find((item) => item.id === clipId);
      const start = numberArgument(args, "source_start", -1); const end = numberArgument(args, "source_end", -1);
      if (clip && start >= 0 && end > start + .04) {
        clip.source_start = rounded(start); clip.source_end = rounded(end);
        clip.reason = `${clip.reason} · AI 副導演已收緊片段。`;
      }
    }
    if (call.name === "insert_b_roll") {
      const start = numberArgument(args, "source_start", 0); const end = numberArgument(args, "source_end", 0);
      const placement = numberArgument(args, "timeline_start", 0);
      const assetId = typeof args.source_asset_id === "string" ? args.source_asset_id : undefined;
      if (assetId && end > start + .04) next.clips.push({
        id: `agent-broll-${crypto.randomUUID()}`, source_asset_id: assetId, track: "b_roll", z_index: numberArgument(args, "z_index", 10),
        audio_enabled: false, audio_effects: [], source_start: rounded(start), source_end: rounded(end), timeline_start: rounded(placement),
        action: "keep", confidence_score: 90, reason: "AI 副導演建議的 B-Roll 覆蓋。", reviewStatus: "kept", kind: "agent_b_roll",
      });
    }
    if (call.name === "adjust_audio_level") {
      const clip = next.clips.find((item) => item.id === String(args.clip_id ?? ""));
      if (clip) clip.audio_gain_db = Math.max(-24, Math.min(24, numberArgument(args, "gain_db")));
    }
    if (call.name === "apply_lut") {
      const key = typeof args.lut_key === "string" ? args.lut_key : "";
      if (key) next.clips.filter((clip) => clip.track !== "audio_overlay").forEach((clip) => {
        clip.lut_key = key; clip.lut_intensity = Math.max(0, Math.min(1, numberArgument(args, "intensity", 1)));
      });
    }
    if (call.name === "add_bgm") {
      const mood = typeof args.mood === "string" ? args.mood : "原創氛圍配樂";
      if (!next.clips.some((clip) => clip.kind === "agent_bgm_request" && clip.reason.includes(mood))) next.clips.push({
        id: `agent-bgm-${crypto.randomUUID()}`, track: "audio_overlay", z_index: 0, audio_enabled: true, audio_effects: [],
        source_start: 0, source_end: Math.max(1, timelineDuration), timeline_start: 0, action: "keep", confidence_score: 85,
        reason: `AI 副導演建議：生成「${mood}」BGM。`, reviewStatus: "kept", kind: "agent_bgm_request",
        audio_gain_db: Math.round(numberArgument(args, "mix_level", .16) * 100),
      });
    }
  }
  return next;
}

export function describeAgentPlan(calls: AgentToolCall[]): string {
  if (!calls.length) return "AI 沒有提出可安全執行的修改。";
  const labels: Record<AgentToolName, string> = { trim_clip: "收緊片段", insert_b_roll: "插入 B-Roll", adjust_audio_level: "調整音量", apply_lut: "套用 LUT", add_bgm: "新增 BGM 意圖" };
  return calls.map((call) => labels[call.name]).join("、");
}
