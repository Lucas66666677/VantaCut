import { create } from "zustand";

import type { TimelineClip, TimelineClipInput } from "@/types/timeline";
import type { ClipTransformAnimation } from "@/types/keyframes";
import type { ClipSpeedCurve } from "@/types/speed-curves";

export interface NudgeCommandInput {
  operation: "adjust_visual" | "set_speed_curve" | "set_transform" | "enable_beat_sync";
  target_clip_ids: string[];
  parameters: Record<string, unknown>;
}

interface TimelineSnapshot {
  clips: TimelineClip[];
  clipAnimations: Record<string, ClipTransformAnimation>;
}

export interface CloudDraftTimeline {
  clips: TimelineClipInput[];
  clip_animations?: Record<string, ClipTransformAnimation>;
  speed_curves?: Record<string, ClipSpeedCurve>;
}

export interface CloudDraftEditorState {
  zoom?: number;
  playhead_time?: number;
}

interface TimelineState {
  clips: TimelineClip[];
  clipAnimations: Record<string, ClipTransformAnimation>;
  speedCurves: Record<string, ClipSpeedCurve>;
  zoom: number;
  playheadTime: number;
  selectedClipId: string | null;
  undoStack: TimelineSnapshot[];
  redoStack: TimelineSnapshot[];
  loadTimeline: (clips: TimelineClipInput[]) => void;
  applyCollaborationTimeline: (clips: TimelineClipInput[]) => void;
  setZoom: (zoom: number) => void;
  setPlayheadTime: (time: number) => void;
  setSelectedClipId: (clipId: string | null) => void;
  confirmCut: (clipId: string) => void;
  rippleDeleteAllSuggestedCuts: () => void;
  keepClip: (clipId: string) => void;
  setAudioEffect: (clipId: string, effect: string, enabled: boolean) => void;
  setClipAnimation: (animation: ClipTransformAnimation) => void;
  setSpeedCurve: (curve: ClipSpeedCurve | null, clipId: string) => void;
  splitClip: (clipId: string, timelineTime: number) => void;
  slipClip: (clipId: string, sourceStart: number, sourceEnd: number) => void;
  slideClip: (clipId: string, deltaSeconds: number) => void;
  setClipTextStyle: (clipId: string, patch: NonNullable<TimelineClip["text_style"]>) => void;
  deleteClip: (clipId: string) => void;
  addBRollClip: (timelineStart: number) => void;
  addSemanticSearchClip: (asset: { id: string; sourceStart: number; sourceEnd: number; filename: string; sourceDuration?: number }) => void;
  upsertBRollClips: (clips: TimelineClipInput[]) => void;
  upsertGrowingIngestClips: (clips: TimelineClipInput[]) => void;
  applyTalkingHeadMarkers: (markers: Array<{ source_start: number; source_end: number; recommendation: "review_cut" | "b_roll"; reason: string; confidence_score: number; fluency_score: number; metrics?: Record<string, number> }>) => void;
  addExternalAudioClip: (sourceAssetId: string, timelineStart: number) => void;
  upsertSyncedAudioClip: (clip: TimelineClipInput) => void;
  muteSourceAssetAudio: (sourceAssetId: string) => void;
  beginOverlayClipMove: () => void;
  previewOverlayClipMove: (clipId: string, timelineStart: number) => void;
  setAudioGain: (clipId: string, gainDb: number, source?: "user" | "ai") => void;
  resetAiModifiedProperty: (clipId: string, property: string) => void;
  applyNudgeCommands: (commands: NudgeCommandInput[]) => void;
  restoreSandboxSnapshot: (snapshot: { clips: TimelineClip[]; clipAnimations: Record<string, ClipTransformAnimation>; speedCurves: Record<string, ClipSpeedCurve> }) => void;
  restoreCloudDraft: (timeline: CloudDraftTimeline, editorState: CloudDraftEditorState) => void;
  undo: () => void;
  redo: () => void;
}

const cloneClips = (clips: TimelineClip[]): TimelineClip[] => clips.map((clip) => ({ ...clip }));
const cloneAnimations = (animations: Record<string, ClipTransformAnimation>): Record<string, ClipTransformAnimation> => structuredClone(animations);

const normaliseClip = (clip: TimelineClipInput, index: number): TimelineClip => ({
  ...clip,
  id: clip.id ?? `ai-clip-${index + 1}`,
  track: clip.track ?? "main_video",
  z_index: clip.z_index ?? (clip.track === "b_roll" ? 10 : 0),
  audio_enabled: clip.audio_enabled ?? clip.track !== "b_roll",
  audio_effects: clip.audio_effects ?? [],
  timeline_start: clip.timeline_start ?? clip.source_start,
  reviewStatus: clip.action === "remove" ? "pending" : "kept",
});

export const useTimelineStore = create<TimelineState>((set, get) => ({
  clips: [],
  clipAnimations: {},
  speedCurves: {},
  zoom: 72,
  playheadTime: 0,
  selectedClipId: null,
  undoStack: [],
  redoStack: [],

  loadTimeline: (clips) => set({
    clips: clips.map(normaliseClip),
    clipAnimations: {},
    speedCurves: {},
    playheadTime: 0,
    selectedClipId: null,
    undoStack: [],
    redoStack: [],
  }),

  applyCollaborationTimeline: (clips) => set({ clips: clips.map(normaliseClip) }),

  setZoom: (zoom) => set({ zoom: Math.max(24, Math.min(240, zoom)) }),
  setPlayheadTime: (time) => set({ playheadTime: Math.max(0, time) }),
  setSelectedClipId: (clipId) => set({ selectedClipId: clipId }),

  confirmCut: (clipId) => {
    const state = get();
    const current = state.clips;
    const next = current.map((clip) => (
      clip.id === clipId ? { ...clip, action: "remove" as const, reviewStatus: "cut" as const } : clip
    ));
    set({
      clips: next,
      undoStack: [...state.undoStack, { clips: cloneClips(current), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  rippleDeleteAllSuggestedCuts: () => {
    const state = get();
    const current = state.clips;
    const hasSuggestions = current.some((clip) => clip.track === "main_video" && clip.action === "remove" && clip.reviewStatus === "pending");
    if (!hasSuggestions) return;
    // Timeline layout subtracts every `cut` duration from following main-track clips,
    // producing ripple delete while source in/out points remain render-safe and immutable.
    const next = current.map((clip) => (
      clip.track === "main_video" && clip.action === "remove" && clip.reviewStatus === "pending"
        ? { ...clip, reviewStatus: "cut" as const }
        : clip
    ));
    set({
      clips: next,
      selectedClipId: null,
      undoStack: [...state.undoStack, { clips: cloneClips(current), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  keepClip: (clipId) => {
    const state = get();
    const current = state.clips;
    const next = current.map((clip) => (
      clip.id === clipId ? { ...clip, action: "keep" as const, reviewStatus: "kept" as const } : clip
    ));
    set({
      clips: next,
      undoStack: [...state.undoStack, { clips: cloneClips(current), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  setAudioEffect: (clipId, effect, enabled) => {
    const state = get();
    const current = state.clips;
    const next = current.map((clip) => {
      if (clip.id !== clipId) return clip;
      const effects = enabled
        ? [...new Set([...clip.audio_effects, effect])]
        : clip.audio_effects.filter((item) => item !== effect);
      return { ...clip, audio_effects: effects };
    });
    set({
      clips: next,
      undoStack: [...state.undoStack, { clips: cloneClips(current), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  setClipAnimation: (animation) => {
    if (!animation.clip_id) return;
    const current = get();
    set({
      clipAnimations: { ...current.clipAnimations, [animation.clip_id]: structuredClone(animation) },
      undoStack: [...current.undoStack, { clips: cloneClips(current.clips), clipAnimations: cloneAnimations(current.clipAnimations) }],
      redoStack: [],
    });
  },

  setSpeedCurve: (curve, clipId) => set((state) => {
    const next = { ...state.speedCurves };
    if (curve) next[clipId] = structuredClone(curve); else delete next[clipId];
    return { speedCurves: next };
  }),

  splitClip: (clipId, timelineTime) => {
    const state = get(); const target = state.clips.find((clip) => clip.id === clipId);
    if (!target) return;
    const timelineStart = target.timeline_start ?? target.source_start;
    const splitSourceTime = target.source_start + (timelineTime - timelineStart);
    // Do not create unusable one-frame fragments at either edge.
    if (splitSourceTime <= target.source_start + .04 || splitSourceTime >= target.source_end - .04) return;
    const left: TimelineClip = { ...target, source_end: Number(splitSourceTime.toFixed(3)) };
    const right: TimelineClip = {
      ...target,
      id: `${target.id}-split-${Date.now()}`,
      source_start: Number(splitSourceTime.toFixed(3)),
      timeline_start: Number((timelineStart + splitSourceTime - target.source_start).toFixed(3)),
      reviewStatus: target.action === "remove" ? "pending" : "kept",
    };
    set({
      clips: state.clips.flatMap((clip) => clip.id === clipId ? [left, right] : [clip]),
      selectedClipId: right.id,
      undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  slipClip: (clipId, sourceStart, sourceEnd) => {
    const state = get();
    const target = state.clips.find((clip) => clip.id === clipId);
    if (!target || sourceEnd - sourceStart <= .04) return;
    const next = state.clips.map((clip) => clip.id === clipId
      ? { ...clip, source_start: Number(sourceStart.toFixed(3)), source_end: Number(sourceEnd.toFixed(3)) }
      : clip);
    set({
      clips: next,
      undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  slideClip: (clipId, deltaSeconds) => {
    const state = get();
    const target = state.clips.find((clip) => clip.id === clipId);
    if (!target || target.track !== "main_video" || Math.abs(deltaSeconds) < .0001) return;
    const ordered = state.clips.filter((clip) => clip.track === "main_video" && clip.reviewStatus !== "cut")
      .sort((left, right) => (left.timeline_start ?? left.source_start) - (right.timeline_start ?? right.source_start));
    const index = ordered.findIndex((clip) => clip.id === clipId);
    if (index < 0) return;
    const previous = ordered[index - 1]; const nextClip = ordered[index + 1];
    const delta = Number(deltaSeconds.toFixed(3));
    const clips = state.clips.map((clip) => {
      if (clip.id === target.id) return { ...clip, timeline_start: Number(((clip.timeline_start ?? clip.source_start) + delta).toFixed(3)) };
      if (clip.id === previous?.id) return { ...clip, source_end: Number((clip.source_end + delta).toFixed(3)) };
      if (clip.id === nextClip?.id) return {
        ...clip,
        source_start: Number((clip.source_start + delta).toFixed(3)),
        timeline_start: Number(((clip.timeline_start ?? clip.source_start) + delta).toFixed(3)),
      };
      return clip;
    });
    set({ clips, undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }], redoStack: [] });
  },

  setClipTextStyle: (clipId, patch) => set((state) => ({
    clips: state.clips.map((clip) => clip.id === clipId ? { ...clip, text_style: { ...(clip.text_style ?? {}), ...patch } } : clip),
  })),

  deleteClip: (clipId) => set((state) => {
    const existing = state.clips.find((clip) => clip.id === clipId);
    if (!existing) return state;
    return {
      clips: state.clips.filter((clip) => clip.id !== clipId),
      selectedClipId: state.selectedClipId === clipId ? null : state.selectedClipId,
      undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    };
  }),

  addBRollClip: (timelineStart) => {
    const state = get();
    const current = state.clips;
    const next: TimelineClip = {
      id: `b-roll-${Date.now()}`,
      track: "b_roll",
      z_index: 10,
      audio_enabled: false,
      audio_effects: [],
      timeline_start: Math.max(0, timelineStart),
      source_start: 0,
      source_end: 4,
      action: "keep",
      confidence_score: 100,
      reason: "使用者加入的 B-Roll 覆蓋畫面。",
      reviewStatus: "kept",
    };
    set({
      clips: [...current, next],
      undoStack: [...state.undoStack, { clips: cloneClips(current), clipAnimations: cloneAnimations(state.clipAnimations) }],
      redoStack: [],
    });
  },

  addSemanticSearchClip: (asset) => {
    const state = get(); const sourceStart = Math.max(0, asset.sourceStart); const sourceEnd = Math.max(sourceStart + .1, asset.sourceEnd);
    const next: TimelineClip = {
      id: `semantic-bin-${Date.now()}`, source_asset_id: asset.id, track: "b_roll", z_index: 10, audio_enabled: false, audio_effects: [],
      timeline_start: state.playheadTime, source_start: sourceStart, source_end: sourceEnd, source_duration: asset.sourceDuration,
      action: "keep", confidence_score: 100, reason: `語意素材庫命中：${asset.filename}`, reviewStatus: "kept", kind: "semantic_media_bin",
    };
    set({ clips: [...state.clips, next], undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }], redoStack: [] });
  },

  upsertBRollClips: (incomingClips) => set((state) => {
    const byId = new Map(state.clips.map((clip) => [clip.id, clip]));
    incomingClips.forEach((clip, index) => {
      const normalised = normaliseClip({ ...clip, track: "b_roll", audio_enabled: false, action: "keep" }, index);
      byId.set(normalised.id, { ...byId.get(normalised.id), ...normalised });
    });
    return { clips: [...byId.values()] };
  }),

  upsertGrowingIngestClips: (incomingClips) => set((state) => {
    const clipsById = new Map(state.clips.map((clip) => [clip.id, clip]));
    incomingClips.forEach((clip, index) => {
      const normalised = normaliseClip({ ...clip, growing: true, action: "keep" }, index);
      clipsById.set(normalised.id, { ...clipsById.get(normalised.id), ...normalised });
    });
    return { clips: [...clipsById.values()].sort((left, right) => (left.timeline_start ?? left.source_start) - (right.timeline_start ?? right.source_start)) };
  }),

  applyTalkingHeadMarkers: (markers) => set((state) => ({
    clips: state.clips.map((clip) => {
      if (clip.track !== "main_video") return clip;
      const marker = markers.find((item) => clip.source_start < item.source_end && clip.source_end > item.source_start);
      if (!marker) return clip;
      return {
        ...clip,
        talking_head_recommendation: marker.recommendation,
        review_flags: [...new Set([...(clip.review_flags ?? []), "Talking-Head：建議審閱呈現狀態"])],
        creator_hints: [...new Set([...(clip.creator_hints ?? []), marker.reason])],
        speaker_state: { confidence_score: marker.confidence_score, fluency_score: marker.fluency_score, assessment_status: "assessed", metrics: marker.metrics },
      };
    }),
  })),

  addExternalAudioClip: (sourceAssetId, timelineStart) => set((state) => ({
    clips: [...state.clips.filter((clip) => clip.kind !== "external_audio_pending"), normaliseClip({
      id: `external-audio-${Date.now()}`, source_asset_id: sourceAssetId, source_start: 0, source_end: 30,
      timeline_start: Math.max(0, timelineStart), track: "audio_overlay", audio_enabled: true, action: "keep",
      confidence_score: 100, reason: "等待 FFT 自動同步的高音質外接音軌", kind: "external_audio_pending",
    }, state.clips.length)],
  })),

  upsertSyncedAudioClip: (incoming) => set((state) => {
    const clip = normaliseClip({ ...incoming, track: "audio_overlay", audio_enabled: true, action: "keep" }, state.clips.length);
    return { clips: [...state.clips.filter((item) => item.kind !== "external_audio_pending" && item.kind !== "synced_external_audio"), clip] };
  }),

  muteSourceAssetAudio: (sourceAssetId) => set((state) => ({
    clips: state.clips.map((clip) => clip.track === "main_video" && clip.source_asset_id === sourceAssetId ? { ...clip, audio_enabled: false, audio_effects: [...new Set([...clip.audio_effects, "muted_after_external_sync"])] } : clip),
  })),

  beginOverlayClipMove: () => set((state) => ({
    undoStack: [...state.undoStack, { clips: cloneClips(state.clips), clipAnimations: cloneAnimations(state.clipAnimations) }],
    redoStack: [],
  })),

  previewOverlayClipMove: (clipId, timelineStart) => set((state) => ({
    clips: state.clips.map((clip) => clip.id === clipId && clip.track !== "main_video"
      ? { ...clip, timeline_start: Math.max(0, Number(timelineStart.toFixed(3))) }
      : clip),
  })),

  setAudioGain: (clipId, gainDb, source = "user") => set((state) => ({
    clips: state.clips.map((clip) => {
      if (clip.id !== clipId) return clip;
      const original = clip.audio_gain_db ?? 0;
      const nextGain = Math.max(-24, Math.min(24, Number(gainDb.toFixed(2))));
      return {
        ...clip,
        audio_gain_db: nextGain,
        ai_modified_properties: source === "ai" ? { ...(clip.ai_modified_properties ?? {}), "audio_gain_db": { original_value: original, current_value: nextGain, source: "ai" } } : clip.ai_modified_properties,
      };
    }),
  })),

  resetAiModifiedProperty: (clipId, property) => set((state) => ({
    clips: state.clips.map((clip) => {
      if (clip.id !== clipId) return clip;
      const modified = clip.ai_modified_properties?.[property];
      if (!modified) return clip;
      const remaining = { ...(clip.ai_modified_properties ?? {}) }; delete remaining[property];
      // Property paths make the indicator extensible: e.g. `visual_adjustments.contrast`
      // and future declarative effect metadata can each be reset without touching the clip.
      const restored = structuredClone(clip) as TimelineClip;
      const path = property.split(".");
      let cursor = restored as unknown as Record<string, unknown>;
      for (const key of path.slice(0, -1)) {
        const current = cursor[key];
        cursor[key] = current && typeof current === "object" ? structuredClone(current) : {};
        cursor = cursor[key] as Record<string, unknown>;
      }
      cursor[path[path.length - 1]] = modified.original_value;
      return { ...restored, ai_modified_properties: remaining };
    }),
  })),

  applyNudgeCommands: (commands) => set((state) => {
    const nextClips = structuredClone(state.clips) as TimelineClip[];
    const nextAnimations = cloneAnimations(state.clipAnimations);
    const nextCurves = structuredClone(state.speedCurves);
    const recordAiChange = (clip: TimelineClip, property: string, nextValue: unknown) => {
      const original = property.split(".").reduce<unknown>((value, key) => value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined, clip);
      clip.ai_modified_properties = { ...(clip.ai_modified_properties ?? {}), [property]: { original_value: original ?? 0, current_value: nextValue, source: "ai_nudge" } };
    };
    for (const command of commands) {
      for (const clip of nextClips.filter((item) => command.target_clip_ids.includes(item.id))) {
        if (command.operation === "adjust_visual") {
          const visual = { ...(clip.visual_adjustments ?? {}) };
          const mapping: Array<[string, "saturation" | "contrast" | "exposure"]> = [["saturation_delta", "saturation"], ["contrast_delta", "contrast"], ["exposure_delta", "exposure"]];
          for (const [parameter, property] of mapping) {
            const delta = Number(command.parameters[parameter]);
            if (!Number.isFinite(delta) || delta === 0) continue;
            const current = Number(visual[property] ?? 0); const next = Number((current + delta).toFixed(2));
            recordAiChange(clip, `visual_adjustments.${property}`, next); visual[property] = next;
          }
          clip.visual_adjustments = visual;
        }
        if (command.operation === "set_speed_curve") {
          const multiplier = Math.max(.8, Math.min(1.3, Number(command.parameters.multiplier) || 1));
          nextCurves[clip.id] = { clip_id: clip.id, preset: "custom", points: [{ position: 0, speed: multiplier }, { position: 1, speed: multiplier }] };
        }
        if (command.operation === "set_transform") {
          const scale = Math.max(.8, Math.min(1.3, Number(command.parameters.scale) || 1));
          const duration = Math.max(.1, clip.source_end - clip.source_start);
          nextAnimations[clip.id] = { clip_id: clip.id, keyframes: [
            { time: 0, value: { x: .5, y: .5, scale: 1, rotation_degrees: 0, z: 0 }, easing: "ease-in-out" },
            { time: duration, value: { x: .5, y: .5, scale, rotation_degrees: 0, z: scale - 1 }, easing: "ease-in-out" },
          ] };
        }
        if (command.operation === "enable_beat_sync") {
          recordAiChange(clip, "beat_sync_enabled", Boolean(command.parameters.enabled));
          clip.beat_sync_enabled = Boolean(command.parameters.enabled);
        }
      }
    }
    return { clips: nextClips, clipAnimations: nextAnimations, speedCurves: nextCurves };
  }),

  restoreSandboxSnapshot: (snapshot) => set({
    clips: cloneClips(snapshot.clips),
    clipAnimations: cloneAnimations(snapshot.clipAnimations),
    speedCurves: structuredClone(snapshot.speedCurves),
    selectedClipId: null,
    undoStack: [],
    redoStack: [],
  }),

  restoreCloudDraft: (timeline, editorState) => set({
    clips: (timeline.clips ?? []).map(normaliseClip),
    clipAnimations: structuredClone(timeline.clip_animations ?? {}),
    speedCurves: structuredClone(timeline.speed_curves ?? {}),
    zoom: Math.max(24, Math.min(240, Number(editorState.zoom ?? 72))),
    playheadTime: Math.max(0, Number(editorState.playhead_time ?? 0)),
    selectedClipId: null,
    undoStack: [],
    redoStack: [],
  }),

  undo: () => {
    const undoStack = get().undoStack;
    const previous = undoStack.at(-1);
    if (!previous) return;
    set({
      clips: cloneClips(previous.clips),
      clipAnimations: cloneAnimations(previous.clipAnimations),
      undoStack: undoStack.slice(0, -1),
      redoStack: [...get().redoStack, { clips: cloneClips(get().clips), clipAnimations: cloneAnimations(get().clipAnimations) }],
    });
  },

  redo: () => {
    const redoStack = get().redoStack;
    const next = redoStack.at(-1);
    if (!next) return;
    set({
      clips: cloneClips(next.clips),
      clipAnimations: cloneAnimations(next.clipAnimations),
      redoStack: redoStack.slice(0, -1),
      undoStack: [...get().undoStack, { clips: cloneClips(get().clips), clipAnimations: cloneAnimations(get().clipAnimations) }],
    });
  },
}));
