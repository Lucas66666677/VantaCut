"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { CutReviewPopover } from "@/features/editor/cut-review-popover";
import { SpeakerFeedbackPopover } from "@/features/editor/speaker-feedback-popover";
import { ClipInspector } from "@/features/editor/clip-inspector";
import { buildTrackLayouts, visibleTimelineDuration } from "@/features/editor/timeline-layout";
import { useTimelineStore } from "@/features/editor/timeline-store";
import { SpeedCurveMiniGraph } from "@/features/editor/speed-curve-mini-graph";
import { useGrowingIngestTimeline } from "@/features/camera-ingest/use-growing-ingest-timeline";
import { RetentionHeatmapTrack } from "@/features/retention/retention-heatmap-track";
import { ColdStorageBanner } from "@/features/storage/cold-storage-banner";
import { FinanceTrack } from "@/features/finance/finance-track";
import { TTSNarrationPanel } from "@/features/editor/tts-narration-panel";
import { BeautyEnhancementPanel } from "@/features/editor/beauty-enhancement-panel";
import { SemanticStockBRollPanel } from "@/features/editor/semantic-stock-broll-panel";
import { AutoNarrativePanel } from "@/features/editor/auto-narrative-panel";
import { VerticalDualLayoutPanel } from "@/features/editor/vertical-dual-layout-panel";
import { MemeGifPanel } from "@/features/editor/meme-gif-panel";
import { SmartAudioRemixToggle } from "@/features/editor/smart-audio-remix-toggle";
import { VisualHooksPanel } from "@/features/editor/visual-hooks-panel";
import { LongToShortsPanel } from "@/features/editor/long-to-shorts-panel";
import { TravelMapPanel } from "@/features/editor/travel-map-panel";
import { FitnessOverlayPanel } from "@/features/editor/fitness-overlay-panel";
import { TalkingHeadConfidencePanel } from "@/features/editor/talking-head-confidence-panel";
import { AudioSyncPanel } from "@/features/editor/audio-sync-panel";
import { animateSnapSpring, clipEdgeSnapPoints, resolveSemanticSnap, triggerTimelineHaptic, useSemanticSnapPoints, type SemanticSnapPoint } from "@/features/editor/semantic-snapping";
import { OptimisticProgress } from "@/features/editor/optimistic-progress";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import { optimisticJobsForClip, useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import { useOptimisticProjectJobs } from "@/features/editor/use-optimistic-project-jobs";
import { useTimelineSandboxHistory, VersionComparisonDialog } from "@/features/editor/non-destructive-history";
import { TransientPlayhead, TransientSnapGuide, setTimelineSnapGuide, useSmoothTimelineZoom, useTimelineAutoScroll, useTimelineVirtualWindow, useZeroRenderScrubbing } from "@/features/editor/timeline-performance";
import { TimelineRulerCanvas } from "@/features/editor/timeline-ruler-canvas";
import { ContextualFloatingToolbar, EditorCommandPalette, EditorKeyboardManager, type EditorCommand } from "@/features/editor/editor-command-center";
import { NudgeCommandBar } from "@/features/editor/nudge-command-bar";
import { AgenticAssistantPanel } from "@/features/editor/agentic-assistant-panel";
import { OmnichannelExportCommandCenter } from "@/features/editor/omnichannel-export-command-center";
import { AgentGhostTrack } from "@/features/editor/agent-ghost-track";
import { useAgentProposalStore } from "@/features/editor/agentic-proposal-store";
import { TextToMusicPanel } from "@/features/editor/text-to-music-panel";
import { useSpacebarPan } from "@/features/editor/use-spacebar-pan";
import { dispatchMediaHoverIntent } from "@/features/editor/media-range-cache";
import { useAudioScrubber } from "@/features/editor/use-audio-scrubber";
import { JklKeyboardManager, JklShortcutHint } from "@/features/editor/jkl-keyboard-navigation";
import { useCollaborativeTimeline } from "@/features/editor/use-collaborative-timeline";
import { CollaborationAvatarStrip, CollaborationPresenceOverlay } from "@/features/editor/collaboration-presence-overlay";
import { AutoPipPanel } from "@/features/editor/auto-pip-panel";
import { SpatialTextPanel } from "@/features/editor/spatial-text-panel";
import { cursorGlyph, resolveSlide, resolveSlip, rubberBandPixels, useSlipSlideToolStore, type EditCursorContext, type TimelineEditTool } from "@/features/editor/slip-slide-editing";
import { WirelessCameraPanel } from "@/features/camera-ingest/wireless-camera-panel";
import { useRetentionPrediction } from "@/features/retention/use-retention-prediction";
import type { ClipLayout, TimelineClipInput } from "@/types/timeline";

const TRACK_LABELS = {
  finance_overlay: "金融圖表／技術指標",
  b_roll: "B-Roll（覆蓋畫面／靜音）",
  main_video: "主影片（畫面＋人聲）",
  audio_overlay: "SFX／音訊覆蓋",
  multicam_video: "無線多機位（即時畫面）",
} as const;

interface TimelineEditorProps {
  timeline: TimelineClipInput[];
  timelineId?: string;
  projectId?: string;
  userId?: string;
  showInspector?: boolean;
  /** Optional signed proxy URL used by the before/after version comparison preview. */
  comparisonPreviewUrl?: string;
  /** Signed extracted-audio proxy used for Web Audio scrubbing; omitted when unavailable. */
  scrubAudioUrl?: string;
}

interface OverlayDragState {
  id: string;
  pointerOffsetSeconds: number;
  latestTime: number;
  snapTarget: number | null;
  rippleOffsets: Record<string, number>;
}

interface ToolbarState { clip: ClipLayout; x: number; y: number; }
interface SlipSlideDragState {
  id: string;
  tool: Exclude<TimelineEditTool, "select">;
  startX: number;
  sourceStart: number;
  sourceEnd: number;
  deltaSeconds: number;
  previewStart: number;
  previewEnd: number;
  overshoot: number;
  atBoundary: boolean;
}

/** Preview displacement only: the dragged Clip creates elastic room without moving siblings in persisted data. */
function rippleOffsetsForDrag(dragged: ClipLayout, proposedStart: number, candidates: ClipLayout[], zoom: number): Record<string, number> {
  const duration = dragged.source_end - dragged.source_start;
  const proposedEnd = proposedStart + duration;
  const spacing = 4 / Math.max(zoom, 1);
  const center = proposedStart + duration / 2;
  return candidates.reduce<Record<string, number>>((offsets, candidate) => {
    if (candidate.id === dragged.id || candidate.reviewStatus === "cut") return offsets;
    const candidateCenter = (candidate.displayStart + candidate.displayEnd) / 2;
    const pushRight = candidateCenter >= center && candidate.displayStart < proposedEnd + spacing;
    const pushLeft = candidateCenter < center && candidate.displayEnd > proposedStart - spacing;
    if (!pushRight && !pushLeft) return offsets;
    const distanceSeconds = pushRight ? proposedEnd + spacing - candidate.displayStart : candidate.displayEnd - (proposedStart - spacing);
    // Cap the preview shift so dense lanes still feel like a soft ripple, not a teleport.
    offsets[candidate.id] = Math.max(-112, Math.min(112, (pushRight ? 1 : -1) * distanceSeconds * zoom));
    return offsets;
  }, {});
}

function collaboratorColor(userId: string): string {
  const palette = ["#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fb923c", "#facc15"];
  return palette[[...userId].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0) % palette.length];
}

export function TimelineEditor({ timeline, timelineId, projectId, userId, showInspector = true, comparisonPreviewUrl, scrubAudioUrl }: TimelineEditorProps) {
  // Never subscribe this large component to playheadTime: it changes at 60 Hz.
  const clips = useTimelineStore((state) => state.clips);
  const clipAnimations = useTimelineStore((state) => state.clipAnimations);
  const speedCurves = useTimelineStore((state) => state.speedCurves);
  const zoom = useTimelineStore((state) => state.zoom);
  const loadTimeline = useTimelineStore((state) => state.loadTimeline);
  const setZoom = useTimelineStore((state) => state.setZoom);
  const setSelectedClipId = useTimelineStore((state) => state.setSelectedClipId);
  const selectedClipId = useTimelineStore((state) => state.selectedClipId);
  const confirmCut = useTimelineStore((state) => state.confirmCut);
  const rippleDeleteAllSuggestedCuts = useTimelineStore((state) => state.rippleDeleteAllSuggestedCuts);
  const keepClip = useTimelineStore((state) => state.keepClip);
  const addBRollClip = useTimelineStore((state) => state.addBRollClip);
  const addExternalAudioClip = useTimelineStore((state) => state.addExternalAudioClip);
  const beginOverlayClipMove = useTimelineStore((state) => state.beginOverlayClipMove);
  const previewOverlayClipMove = useTimelineStore((state) => state.previewOverlayClipMove);
  const restoreSandboxSnapshot = useTimelineStore((state) => state.restoreSandboxSnapshot);
  const splitClip = useTimelineStore((state) => state.splitClip);
  const slipClip = useTimelineStore((state) => state.slipClip);
  const slideClip = useTimelineStore((state) => state.slideClip);
  const setClipTextStyle = useTimelineStore((state) => state.setClipTextStyle);
  const applyNudgeCommands = useTimelineStore((state) => state.applyNudgeCommands);
  const setAudioEffect = useTimelineStore((state) => state.setAudioEffect);
  const deleteClip = useTimelineStore((state) => state.deleteClip);
  const [selectedClip, setSelectedClip] = useState<ClipLayout | null>(null);
  const [speakerHintClip, setSpeakerHintClip] = useState<ClipLayout | null>(null);
  const [inspectedClipId, setInspectedClipId] = useState<string | null>(null);
  const [overlayDrag, setOverlayDrag] = useState<OverlayDragState | null>(null);
  const [slipSlideDrag, setSlipSlideDrag] = useState<SlipSlideDragState | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [toolbar, setToolbar] = useState<ToolbarState | null>(null);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [historyAnimation, setHistoryAnimation] = useState<"undo" | "redo" | null>(null);
  const [constraintShakeClipId, setConstraintShakeClipId] = useState<string | null>(null);
  const editTool = useSlipSlideToolStore((state) => state.tool);
  const setEditTool = useSlipSlideToolStore((state) => state.setTool);
  const setEditCursor = useSlipSlideToolStore((state) => state.setCursor);
  const setGhostPreview = useSlipSlideToolStore((state) => state.setGhostPreview);
  const editCursor = useSlipSlideToolStore((state) => state.cursor);
  const hapticPointRef = useRef<string | null>(null);
  const springCancelRef = useRef<(() => void) | null>(null);
  const timelineScrollerRef = useRef<HTMLDivElement>(null);
  const timelineSurfaceRef = useRef<HTMLDivElement>(null);
  const { spaceHeld, isPanning } = useSpacebarPan({ viewportRef: timelineScrollerRef, contentRef: timelineSurfaceRef });
  const { update: updateAutoScroll, stop: stopAutoScroll } = useTimelineAutoScroll(timelineScrollerRef);
  const layouts = useMemo(() => buildTrackLayouts(clips), [clips]);
  const virtualLayouts = useTimelineVirtualWindow(layouts, zoom, timelineScrollerRef, overlayDrag?.id ?? inspectedClipId);
  const onTimelineWheel = useSmoothTimelineZoom({ zoom, setZoom, scrollerRef: timelineScrollerRef, surfaceRef: timelineSurfaceRef });
  const duration = useMemo(() => visibleTimelineDuration(layouts), [layouts]);
  const canvasWidth = duration * zoom + 160;
  const inspectedClip = useMemo(() => {
    for (const trackClips of layouts.values()) {
      const match = trackClips.find((clip) => clip.id === inspectedClipId);
      if (match) return match;
    }
    return null;
  }, [inspectedClipId, layouts]);
  const sourceAssetId = timeline.find((clip) => clip.track !== "b_roll" && clip.source_asset_id)?.source_asset_id;
  const autoNarrativeAssetIds = useMemo(() => [...new Set(timeline.filter((clip) => clip.track !== "b_roll" && clip.source_asset_id).map((clip) => clip.source_asset_id!))], [timeline]);
  useGrowingIngestTimeline(projectId, timelineId);
  const { prediction, loading: retentionLoading, error: retentionError, refresh: refreshRetention } = useRetentionPrediction(timelineId, userId);
  const projectStatus = useOptimisticProjectJobs(projectId);
  const optimisticJobs = useOptimisticEffectsStore((state) => state.jobs);
  const agentProposal = useAgentProposalStore((state) => state.proposal);
  const { points: semanticPoints, preferences: snapPreferences, toggle: toggleSnapPreference } = useSemanticSnapPoints(timelineId, userId);
  const edgePoints = useMemo(() => clipEdgeSnapPoints(clips), [clips]);
  const allSnapPoints = useMemo(() => [...edgePoints, ...semanticPoints], [edgePoints, semanticPoints]);
  const sandboxSnapshot = useMemo(() => ({ clips, clipAnimations, speedCurves }), [clips, clipAnimations, speedCurves]);
  const sandbox = useTimelineSandboxHistory(sandboxSnapshot, restoreSandboxSnapshot);
  const audioScrubber = useAudioScrubber(scrubAudioUrl);
  const collaboration = useCollaborativeTimeline({
    timelineId,
    currentUser: { id: userId ?? "local-editor", name: userId ? `剪輯師 ${userId.slice(0, 4)}` : "本機剪輯師", color: collaboratorColor(userId ?? "local-editor") },
    initialTimeline: timeline,
  });
  const collaborationLayouts = useMemo(() => [...layouts.values()].flat(), [layouts]);

  useEffect(() => {
    // A restored cloud draft wins over the server-provided initial timeline.
    // This component mounts after the workspace shell, where cloud restoration begins.
    if (useTimelineStore.getState().clips.length === 0) loadTimeline(timeline);
  }, [loadTimeline, timeline]);

  useEffect(() => {
    if (selectedClip && !clips.some((clip) => clip.id === selectedClip.id && clip.reviewStatus === "pending")) {
      setSelectedClip(null);
    }
  }, [clips, selectedClip]);

  useEffect(() => {
    if (toolbar && !clips.some((clip) => clip.id === toolbar.clip.id)) setToolbar(null);
  }, [clips, toolbar]);

  useEffect(() => () => springCancelRef.current?.(), []);

  useEffect(() => {
    if (!historyAnimation) return;
    const timer = window.setTimeout(() => setHistoryAnimation(null), 420);
    return () => window.clearTimeout(timer);
  }, [historyAnimation]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true']") || event.key !== "Backspace" || !inspectedClip) return;
      event.preventDefault(); deleteClip(inspectedClip.id); setToolbar(null); setInspectedClipId(null); setSelectedClip(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteClip, inspectedClip]);

  useEffect(() => {
    const onHistoryShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true'], [role='dialog']")) return;
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) {
        if (!sandbox.canRedo) return;
        setHistoryAnimation("redo");
        sandbox.redo();
        return;
      }
      if (!sandbox.canUndo) return;
      setHistoryAnimation("undo");
      sandbox.undo();
    };
    window.addEventListener("keydown", onHistoryShortcut);
    return () => window.removeEventListener("keydown", onHistoryShortcut);
  }, [sandbox]);

  useEffect(() => {
    const onToolShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true'], [role='dialog']") || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "y") { event.preventDefault(); setEditTool(editTool === "slip" ? "select" : "slip"); return; }
      if (key === "u") { event.preventDefault(); setEditTool(editTool === "slide" ? "select" : "slide"); return; }
      if (key === "escape" && editTool !== "select") { event.preventDefault(); setEditTool("select"); setGhostPreview(null); setSlipSlideDrag(null); }
    };
    window.addEventListener("keydown", onToolShortcut, { capture: true });
    return () => window.removeEventListener("keydown", onToolShortcut, { capture: true });
  }, [editTool, setEditTool, setGhostPreview]);

  useEffect(() => () => setGhostPreview(null), [setGhostPreview]);

  const resolveSnap = useCallback((rawTime: number) => {
    const playheadPoint: SemanticSnapPoint = {
      id: "editor-playhead",
      time_seconds: useTimelineStore.getState().playheadTime,
      type: "clip_edge",
      strength: .92,
      label: "播放頭",
    };
    const resolved = resolveSemanticSnap(rawTime, zoom, [...allSnapPoints, playheadPoint], snapPreferences);
    if (resolved.point?.id && hapticPointRef.current !== resolved.point.id) {
      triggerTimelineHaptic(resolved.point.type === "action_peak" ? 12 : 7);
      hapticPointRef.current = resolved.point.id;
    }
    if (!resolved.point) hapticPointRef.current = null;
    return resolved;
  }, [allSnapPoints, snapPreferences, zoom]);

  useZeroRenderScrubbing({ surfaceRef: timelineSurfaceRef, scrollerRef: timelineScrollerRef, zoom, duration, resolve: resolveSnap, onStart: (time) => audioScrubber.start(time), onScrub: (time, velocity) => audioScrubber.scrub(time, velocity), onEnd: () => { audioScrubber.stop(); hapticPointRef.current = null; } });

  const executeEditorCommand = useCallback(async (command: EditorCommand, explicitClip?: ClipLayout) => {
    const clip = explicitClip ?? toolbar?.clip ?? inspectedClip;
    if (!clip) return;
    if (command === "split") { splitClip(clip.id, useTimelineStore.getState().playheadTime); return; }
    if (command === "speed") {
      useTimelineStore.getState().setSpeedCurve({ clip_id: clip.id, preset: "hero", points: [{ position: 0, speed: 1 }, { position: .35, speed: .55 }, { position: .62, speed: 1.55 }, { position: 1, speed: 1 }] }, clip.id);
      return;
    }
    if (command === "noir") {
      const optimistic = useOptimisticEffectsStore.getState();
      const optimisticId = optimistic.begin({ kind: "filter", clipId: clip.id, mediaAssetId: clip.source_asset_id, message: "黑白電影感已先套用預覽。" });
      applyNudgeCommands([{ operation: "adjust_visual", target_clip_ids: [clip.id], parameters: { saturation_delta: -100, contrast_delta: 12, exposure_delta: -.1 } }]);
      optimistic.complete(optimisticId, "黑白電影感已套用；可隨時從版本樹回復。");
      return;
    }
    if (command === "text_font") { setClipTextStyle(clip.id, { font_family: "Noto Sans TC Black" }); return; }
    if (command === "text_animation") { setClipTextStyle(clip.id, { animation: "spring_pop" }); return; }
    if (command === "text_color") { setClipTextStyle(clip.id, { color: "#FDE047" }); return; }
    if (command === "matting") {
      if (!clip.source_asset_id || !userId) return;
      const optimistic = useOptimisticEffectsStore.getState();
      const optimisticId = optimistic.begin({ kind: "matting", clipId: clip.id, mediaAssetId: clip.source_asset_id, message: "已先套用人物去背預覽。" });
      try {
        const response = await authenticatedFetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1/media/${clip.source_asset_id}/matting`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "click", frame_time: useTimelineStore.getState().playheadTime, points: [{ x: .5, y: .5, positive: true }], use_proxy: true, feather_pixels: 2.5, despill_strength: .65 }) });
        const result = await response.json() as { task_id?: string; detail?: string };
        if (!response.ok || !result.task_id) throw new Error(result.detail ?? "無法建立去背任務");
        optimistic.attachTask(optimisticId, result.task_id);
      } catch (error) { optimistic.fail(optimisticId, error instanceof Error ? error.message : "去背任務失敗"); }
      return;
    }
    if (command === "noise_reduction") {
      const enabled = !clip.audio_effects.includes("noise_reduction"); setAudioEffect(clip.id, "noise_reduction", enabled);
      if (!timelineId || !enabled) return;
      const optimistic = useOptimisticEffectsStore.getState();
      const optimisticId = optimistic.begin({ kind: "noise_reduction", clipId: clip.id, mediaAssetId: clip.source_asset_id, message: "已先套用乾淨人聲預覽，正在精修音訊。" });
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/api/v1/timelines/${timelineId}/clips/${clip.id}/noise-reduction`, { method: "POST" });
        const payload = await response.json() as { task_id?: string; detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "無法建立降噪任務");
        if (payload.task_id) optimistic.attachTask(optimisticId, payload.task_id);
        else optimistic.complete(optimisticId, "降噪設定已同步。");
      } catch (error) {
        setAudioEffect(clip.id, "noise_reduction", false);
        optimistic.fail(optimisticId, error instanceof Error ? error.message : "降噪任務失敗");
      }
    }
  }, [applyNudgeCommands, inspectedClip, setAudioEffect, setClipTextStyle, splitClip, timelineId, toolbar?.clip, userId]);

  const rejectMusicStretch = useCallback((clipId: string) => {
    setConstraintShakeClipId(clipId);
    triggerTimelineHaptic(10);
    window.setTimeout(() => setConstraintShakeClipId((current) => current === clipId ? null : current), 390);
  }, []);
  const beginConstrainedMusicStretch = useCallback((event: ReactPointerEvent<HTMLSpanElement>, clipId: string) => {
    event.preventDefault(); event.stopPropagation();
    const handle = event.currentTarget; const startX = event.clientX;
    handle.setPointerCapture(event.pointerId);
    const move = (moveEvent: PointerEvent) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const attempted = moveEvent.clientX - startX;
      // A small elastic travel communicates resistance while preserving the immutable source duration.
      handle.style.transform = `translateX(${Math.max(-4, Math.min(4, attempted * .12))}px)`;
      if (Math.abs(attempted) > 2) rejectMusicStretch(clipId);
    };
    const release = () => {
      handle.style.transform = "";
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", release); handle.removeEventListener("pointercancel", release);
    };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", release); handle.addEventListener("pointercancel", release);
  }, [rejectMusicStretch]);

  const updateEditCursor = useCallback((event: ReactPointerEvent<HTMLElement>, tool: TimelineEditTool) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left; const y = event.clientY - rect.top;
    const context: EditCursorContext = x < 12 ? "left-edge" : x > rect.width - 12 ? "right-edge" : y < rect.height * .38 ? "upper" : y > rect.height * .72 ? "lower" : "body";
    setEditCursor({ x: event.clientX, y: event.clientY, context });
  }, [setEditCursor]);

  const beginSlipSlide = useCallback((event: ReactPointerEvent<HTMLButtonElement>, clip: ClipLayout) => {
    if (editTool === "select" || clip.track !== "main_video") return false;
    event.preventDefault(); event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId);
    collaboration.beginClipIntent(clip.id);
    setSlipSlideDrag({ id: clip.id, tool: editTool, startX: event.clientX, sourceStart: clip.source_start, sourceEnd: clip.source_end, deltaSeconds: 0, previewStart: clip.source_start, previewEnd: clip.source_end, overshoot: 0, atBoundary: false });
    if (editTool === "slip") setGhostPreview({ clipId: clip.id, sourceAssetId: clip.source_asset_id, inTimeMs: clip.source_start * 1_000, outTimeMs: clip.source_end * 1_000 });
    return true;
  }, [collaboration, editTool, setGhostPreview]);

  const moveSlipSlide = useCallback((event: ReactPointerEvent<HTMLButtonElement>, clip: ClipLayout) => {
    const drag = slipSlideDrag;
    if (!drag || drag.id !== clip.id || !event.currentTarget.hasPointerCapture(event.pointerId)) return false;
    event.preventDefault(); event.stopPropagation();
    const requestedDelta = (event.clientX - drag.startX) / zoom;
    if (drag.tool === "slip") {
      const resolution = resolveSlip(clip, requestedDelta);
      setSlipSlideDrag({ ...drag, deltaSeconds: resolution.sourceStart - drag.sourceStart, previewStart: resolution.sourceStart, previewEnd: resolution.sourceEnd, overshoot: resolution.overshoot, atBoundary: resolution.atBoundary });
      setGhostPreview({ clipId: clip.id, sourceAssetId: clip.source_asset_id, inTimeMs: resolution.sourceStart * 1_000, outTimeMs: resolution.sourceEnd * 1_000 });
    } else {
      const ordered = clips.filter((candidate) => candidate.track === "main_video" && candidate.reviewStatus !== "cut").sort((left, right) => (left.timeline_start ?? left.source_start) - (right.timeline_start ?? right.source_start));
      const index = ordered.findIndex((candidate) => candidate.id === clip.id);
      const resolution = resolveSlide(ordered[index - 1], ordered[index + 1], requestedDelta);
      setSlipSlideDrag({ ...drag, deltaSeconds: resolution.delta, previewStart: drag.sourceStart, previewEnd: drag.sourceEnd, overshoot: resolution.overshoot, atBoundary: resolution.atBoundary });
    }
    updateAutoScroll(event.clientX);
    return true;
  }, [clips, setGhostPreview, slipSlideDrag, updateAutoScroll, zoom]);

  const finishSlipSlide = useCallback((event: ReactPointerEvent<HTMLButtonElement>, clip: ClipLayout) => {
    const drag = slipSlideDrag;
    if (!drag || drag.id !== clip.id) return false;
    event.preventDefault(); event.stopPropagation();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (drag.tool === "slip") slipClip(clip.id, drag.previewStart, drag.previewEnd); else slideClip(clip.id, drag.deltaSeconds);
    stopAutoScroll(); collaboration.endClipIntent(clip.id); setSlipSlideDrag(null); setGhostPreview(null);
    return true;
  }, [collaboration, setGhostPreview, slipClip, slideClip, slipSlideDrag, stopAutoScroll]);

  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 shadow-xl">
      <ColdStorageBanner projectId={projectId} userId={userId} />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">AI 粗剪審閱時間軸</h2>
          <p className="mt-1 text-xs text-zinc-400">紅色虛線區塊為 AI 偵測到的靜音、贅詞或重複內容；拖曳時會吸附到語意節點。</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-zinc-700 text-xs">
            <button onClick={() => setEditTool(editTool === "slip" ? "select" : "slip")} className={`px-2.5 py-1.5 transition ${editTool === "slip" ? "bg-cyan-400 text-zinc-950" : "text-zinc-300 hover:bg-zinc-800"}`} title="Y · 保持片段長度，滑動來源 In/Out">Slip <kbd className="ml-1 opacity-70">Y</kbd></button>
            <button onClick={() => setEditTool(editTool === "slide" ? "select" : "slide")} className={`border-l border-zinc-700 px-2.5 py-1.5 transition ${editTool === "slide" ? "bg-violet-400 text-zinc-950" : "text-zinc-300 hover:bg-zinc-800"}`} title="U · 移動片段，連帶修剪相鄰片段">Slide <kbd className="ml-1 opacity-70">U</kbd></button>
          </div>
          <button
            draggable
            onDragStart={(event) => event.dataTransfer.setData("application/x-broll", "new")}
            className="cursor-grab rounded-md border border-violet-400/60 bg-violet-500/20 px-3 py-1.5 text-xs text-violet-100 active:cursor-grabbing"
          >
            拖曳 B-Roll 片段
          </button>
          <button disabled={!sandbox.canUndo} onClick={() => { setHistoryAnimation("undo"); sandbox.undo(); }} className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40">退回版本</button>
          <button disabled={!sandbox.canRedo} onClick={() => { setHistoryAnimation("redo"); sandbox.redo(); }} className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40">前進分支</button>
          <button onClick={() => setHistoryOpen((open) => !open)} className="rounded-md border border-amber-400/60 bg-amber-400/10 px-3 py-1.5 text-xs text-amber-100">
            版本樹 ({sandbox.nodes.length})
          </button>
          <button
            disabled={!clips.some((clip) => clip.track === "main_video" && clip.action === "remove" && clip.reviewStatus === "pending")}
            onClick={() => { rippleDeleteAllSuggestedCuts(); triggerTimelineHaptic(18); }}
            className="rounded-md border border-red-400/60 bg-red-500/10 px-3 py-1.5 text-xs text-red-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            一鍵刪除所有廢話
          </button>
          <button disabled={!timelineId || !userId || retentionLoading} onClick={() => void refreshRetention()} className="rounded-md border border-emerald-400/60 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40">
            {retentionLoading ? "預測中…" : "更新留存預測"}
          </button>
          <label className="ml-2 flex items-center gap-2 text-xs text-zinc-300">
            Zoom
            <input aria-label="Timeline zoom" type="range" min="24" max="240" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} />
          </label>
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-zinc-300">
        <span className="rounded bg-cyan-500/15 px-2 py-1 text-cyan-200">語意磁吸</span>
        {(["downbeat", "speech_pause", "action_peak"] as const).map((type) => (
          <button key={type} onClick={() => toggleSnapPreference(type)} className={`rounded border px-2 py-1 transition ${snapPreferences[type] ? "border-cyan-300/70 bg-cyan-400/15 text-cyan-100" : "border-zinc-700 text-zinc-500"}`}>
            {{ downbeat: "重拍", speech_pause: "語句停頓", action_peak: "動作／槍聲高光" }[type]}
          </button>
        ))}
        <span className="text-zinc-500">片段邊緣固定啟用 · {semanticPoints.length} 個 AI 特徵點</span>
      </div>
      <OptimisticProgress status={projectStatus} className="mb-3" />
      <WirelessCameraPanel timelineId={timelineId} projectId={projectId} userId={userId} />

      {historyOpen && (
        <div className="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-zinc-200">
          <div className="mb-2 flex items-center justify-between"><span className="font-medium text-amber-100">非破壞性版本樹</span><span className="text-zinc-500">每次編輯都保留為可回復分支</span></div>
          <div className="max-h-28 space-y-1 overflow-y-auto pr-1">
            {sandbox.nodes.map((node) => (
              <div key={node.id} className={`flex items-center gap-2 rounded px-2 py-1 ${node.id === sandbox.currentId ? "bg-amber-300/15" : "bg-zinc-950/50"}`}>
                <span className="text-zinc-500">{node.parentId ? "↳" : "●"}</span>
                <span className="min-w-0 flex-1 truncate">{node.label}</span>
                <button onClick={() => sandbox.checkout(node.id)} className="text-cyan-200 hover:text-cyan-100">切換</button>
                {sandbox.currentId && node.id !== sandbox.currentId && <button onClick={() => sandbox.setComparison({ before: node.id, after: sandbox.currentId! })} className="text-amber-200 hover:text-amber-100">比較</button>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div ref={timelineScrollerRef} onWheel={onTimelineWheel} className={`overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 ${spaceHeld ? isPanning ? "cursor-grabbing" : "cursor-grab" : ""}`}>
        <div
          ref={timelineSurfaceRef}
          className={`relative min-h-48 select-none will-change-transform ${editTool === "select" ? "" : "cursor-none"}`}
          style={{ width: canvasWidth, transform: "var(--editor-pan, translate3d(0, 0, 0)) scaleX(var(--timeline-zoom-scale, 1))", transformOrigin: "var(--timeline-zoom-origin, 0 0)" }}
          onPointerMoveCapture={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            collaboration.sendPresence({ cursor: { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)), surface: "timeline" } });
          }}
          onPointerLeave={() => collaboration.sendPresence({ cursor: undefined }, true)}
        >
          <TimelineRulerCanvas scrollerRef={timelineScrollerRef} zoom={zoom} duration={duration} />
          <CollaborationAvatarStrip peers={collaboration.peers} />
          {editTool !== "select" && editCursor && <span aria-hidden className="pointer-events-none fixed z-[100] grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-cyan-100/80 bg-zinc-950/90 text-sm font-bold text-cyan-100 shadow-[0_3px_14px_rgba(34,211,238,.45)]" style={{ left: editCursor.x + 14, top: editCursor.y + 14 }}>{cursorGlyph(editTool, editCursor.context)}</span>}

          <div className="relative space-y-2 p-2">
            {[...layouts.entries()].map(([track, allTrackClips]) => {
              const trackClips = virtualLayouts.get(track) ?? [];
              const trackHasBoundaryBump = slipSlideDrag?.atBoundary && trackClips.some((clip) => clip.id === slipSlideDrag.id);
              return (
              <div
                key={track}
                data-timeline-track={track}
                className={`relative h-16 rounded-lg transition-shadow ${track === "b_roll" ? "bg-violet-950/25 ring-1 ring-inset ring-violet-500/20" : track === "finance_overlay" ? "bg-emerald-950/25 ring-1 ring-inset ring-emerald-500/20" : "bg-zinc-900/70"} ${trackHasBoundaryBump ? "ring-2 ring-red-400/90 shadow-[inset_0_0_20px_rgba(248,113,113,.5)]" : ""}`}
                onDragOver={(event) => {
                  if (track === "b_roll" || track === "audio_overlay") event.preventDefault();
                }}
                onDrop={(event) => {
                  if (track === "audio_overlay") {
                    const externalAudioId = event.dataTransfer.getData("application/x-external-audio");
                    if (!externalAudioId) return;
                    event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); addExternalAudioClip(externalAudioId, (event.clientX - rect.left) / zoom); return;
                  }
                  if (track !== "b_roll" || event.dataTransfer.getData("application/x-broll") !== "new") return;
                  event.preventDefault();
                  const rect = event.currentTarget.getBoundingClientRect();
                  addBRollClip((event.clientX - rect.left) / zoom);
                }}
              >
                <span className="absolute left-2 top-2 z-10 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{TRACK_LABELS[track]}</span>
                {track === "b_roll" && !allTrackClips.length && (
                  <span className="absolute left-36 top-2 text-[10px] text-violet-300/70">將 B-Roll 拖曳到此處</span>
                )}
                {track === "finance_overlay" && !allTrackClips.length && (
                  <span className="absolute left-40 top-2 text-[10px] text-emerald-300/70">以金融軌道面板加入 K 線與技術指標</span>
                )}
                {track === "audio_overlay" && !allTrackClips.length && <span className="absolute left-36 top-2 text-[10px] text-cyan-300/70">將高音質外接收音拖曳到此處，再點擊自動同步</span>}
                <AnimatePresence initial={false} mode="popLayout">
                {trackClips.filter((clip) => clip.reviewStatus !== "cut").map((clip) => {
                  const isRemoval = clip.action === "remove" && clip.reviewStatus === "pending";
                  const isTalkingHeadCut = clip.talking_head_recommendation === "review_cut";
                  const isBRoll = clip.track === "b_roll";
                  const isSemanticStockBRoll = isBRoll && clip.kind === "semantic_stock_broll";
                  const isSfx = clip.track === "audio_overlay" && clip.id.startsWith("auto-sfx-");
                  const isMusicClip = clip.track === "audio_overlay" && (clip.kind?.includes("music") || clip.id.includes("music") || clip.reason.includes("BGM"));
                  const canExtendMusic = clip.audio_effects.includes("ai_music_extend");
                  const issueLabel = clip.issue_types?.map((item) => ({ silence: "靜音", filler_word: "贅詞", repetition: "重複" })[item]).join("／");
                  const speedCurve = speedCurves[clip.id];
                  const optimisticJob = optimisticJobsForClip(optimisticJobs, clip.id, clip.source_asset_id).sort((left, right) => right.createdAt - left.createdAt)[0];
                  const optimisticPending = optimisticJob?.state === "optimistic" || optimisticJob?.state === "processing";
                  const optimisticCompleted = optimisticJob?.state === "completed";
                  const optimisticFailed = optimisticJob?.state === "failed";
                  const optimisticLabel = optimisticJob ? ({ matting: "AI 去背", inpainting: "AI 修復", noise_reduction: "AI 降噪", studio_sound: "Studio Sound", filter: "質感濾鏡" }[optimisticJob.kind]) : null;
                  const isDraggingClip = overlayDrag?.id === clip.id;
                  const isSlipSliding = slipSlideDrag?.id === clip.id;
                  const rippleOffset = overlayDrag?.rippleOffsets[clip.id] ?? 0;
                  const bumperOffset = isSlipSliding ? rubberBandPixels(slipSlideDrag.overshoot, zoom) : 0;
                  const magnetized = isDraggingClip && overlayDrag?.snapTarget !== null;
                  const intentPeer = collaboration.peers.find((peer) => peer.lockedClipId === clip.id);
                  return (
                    <motion.div
                      key={clip.id}
                      data-timeline-clip={clip.id}
                      layout="position"
                      initial={historyAnimation === "undo" ? { y: 10, opacity: 0 } : historyAnimation === "redo" ? { y: -6, opacity: 0 } : { opacity: 0, scale: .96 }}
                      // A tiny inward snap feels more like magnetic tension than a generic hover grow.
                      animate={{ x: constraintShakeClipId === clip.id ? [rippleOffset, rippleOffset - 7, rippleOffset + 6, rippleOffset - 3, rippleOffset] : isSlipSliding && slipSlideDrag?.atBoundary ? [bumperOffset, bumperOffset - 5, bumperOffset + 3, bumperOffset] : rippleOffset, scale: magnetized ? .975 : isSlipSliding ? .985 : 1, y: 0, opacity: 1 }}
                      exit={{ scale: .8, opacity: 0, transition: { duration: .16, ease: "easeOut" } }}
                      transition={{ type: "spring", stiffness: 460, damping: 34, mass: .55 }}
                      className={`absolute top-7 will-change-transform ${isDraggingClip || isSlipSliding ? "z-20" : "z-0"}`}
                      style={{
                        left: clip.displayStart * zoom,
                        width: Math.max(12, (clip.displayEnd - clip.displayStart) * zoom),
                      }}
                    >
                      {speedCurve && <div className="absolute left-0 top-0" style={{ width: Math.max(12, (clip.displayEnd - clip.displayStart) * zoom) }}><SpeedCurveMiniGraph curve={speedCurve} width={Math.max(12, (clip.displayEnd - clip.displayStart) * zoom)} /></div>}
                    <button
                      onPointerEnter={(event) => {
                        if (editTool !== "select") updateEditCursor(event, editTool);
                        if (!clip.source_asset_id) return;
                        const bounds = event.currentTarget.getBoundingClientRect();
                        const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / Math.max(1, bounds.width)));
                        dispatchMediaHoverIntent({ assetId: clip.source_asset_id, timeMs: (clip.source_start + (clip.source_end - clip.source_start) * ratio) * 1_000 });
                      }}
                      onPointerLeave={() => { dispatchMediaHoverIntent(null); if (editTool !== "select") setEditCursor(null); }}
                      onPointerDown={(event) => {
                        event.stopPropagation();
                        if (beginSlipSlide(event, clip)) return;
                        if (clip.track === "main_video") return;
                        collaboration.beginClipIntent(clip.id);
                        const trackElement = event.currentTarget.closest("[data-timeline-track]") as HTMLDivElement | null;
                        if (!trackElement) return;
                        const trackRect = trackElement.getBoundingClientRect();
                        const pointerTime = (event.clientX - trackRect.left) / zoom;
                        event.currentTarget.setPointerCapture(event.pointerId);
                        beginOverlayClipMove();
                        setOverlayDrag({ id: clip.id, pointerOffsetSeconds: pointerTime - clip.displayStart, latestTime: clip.timeline_start ?? clip.displayStart, snapTarget: null, rippleOffsets: {} });
                      }}
                      onPointerMove={(event) => {
                        if (editTool !== "select") updateEditCursor(event, editTool);
                        if (moveSlipSlide(event, clip)) return;
                        if (!overlayDrag || overlayDrag.id !== clip.id || !event.currentTarget.hasPointerCapture(event.pointerId)) {
                          if (clip.source_asset_id) {
                            const bounds = event.currentTarget.getBoundingClientRect();
                            const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / Math.max(1, bounds.width)));
                            dispatchMediaHoverIntent({ assetId: clip.source_asset_id, timeMs: (clip.source_start + (clip.source_end - clip.source_start) * ratio) * 1_000 });
                          }
                          return;
                        }
                        event.stopPropagation();
                        const trackElement = event.currentTarget.closest("[data-timeline-track]") as HTMLDivElement | null;
                        if (!trackElement) return;
                        const rawStart = Math.max(0, (event.clientX - trackElement.getBoundingClientRect().left) / zoom - overlayDrag.pointerOffsetSeconds);
                        const resolved = resolveSnap(rawStart);
                        previewOverlayClipMove(clip.id, resolved.time);
                        setTimelineSnapGuide(resolved.point ? { time: resolved.point.time_seconds, label: resolved.point.label, active: true } : { time: resolved.time, label: "", active: false });
                        updateAutoScroll(event.clientX);
                        setOverlayDrag({ ...overlayDrag, latestTime: resolved.time, snapTarget: resolved.point?.time_seconds ?? null, rippleOffsets: rippleOffsetsForDrag(clip, resolved.time, trackClips, zoom) });
                      }}
                      onPointerUp={(event) => {
                        if (finishSlipSlide(event, clip)) return;
                        if (!overlayDrag || overlayDrag.id !== clip.id) return;
                        event.stopPropagation();
                        event.currentTarget.releasePointerCapture(event.pointerId);
                        if (overlayDrag.snapTarget !== null) {
                          springCancelRef.current?.();
                          springCancelRef.current = animateSnapSpring(overlayDrag.latestTime, overlayDrag.snapTarget, (value) => previewOverlayClipMove(clip.id, value));
                        }
                        stopAutoScroll();
                        collaboration.updateClip(clip.id, { timeline_start: Number(overlayDrag.latestTime.toFixed(3)) });
                        collaboration.endClipIntent(clip.id);
                        setOverlayDrag(null);
                        setTimelineSnapGuide({ time: 0, label: "", active: false });
                        hapticPointRef.current = null;
                      }}
                      onPointerCancel={() => { stopAutoScroll(); collaboration.endClipIntent(clip.id); if (slipSlideDrag?.id === clip.id) { setSlipSlideDrag(null); setGhostPreview(null); } setOverlayDrag(null); setTimelineSnapGuide({ time: 0, label: "", active: false }); hapticPointRef.current = null; }}
                      onClick={(event) => {
                        if (editTool !== "select") return;
                        setInspectedClipId(clip.id);
                        setSelectedClipId(clip.id);
                        const surfaceBounds = timelineSurfaceRef.current?.getBoundingClientRect();
                        const buttonBounds = event.currentTarget.getBoundingClientRect();
                        if (surfaceBounds) setToolbar({ clip, x: buttonBounds.left - surfaceBounds.left + buttonBounds.width / 2, y: buttonBounds.top - surfaceBounds.top });
                        if (isRemoval) setSelectedClip(clip);
                        else if ((clip.creator_hints?.length ?? 0) > 0 || (clip.review_flags?.length ?? 0) > 0) setSpeakerHintClip(clip);
                      }}
                      className={`absolute top-0 h-7 rounded border px-2 text-left text-[10px] font-medium transition ${isDraggingClip || isSlipSliding ? "cursor-grabbing shadow-[0_8px_22px_rgba(0,0,0,.45)]" : editTool !== "select" && clip.track === "main_video" ? "cursor-none" : ""} ${isSlipSliding && slipSlideDrag?.atBoundary ? "border-red-100 shadow-[0_0_20px_rgba(248,113,113,.9)]" : ""} ${magnetized ? "border-cyan-100 shadow-[0_0_18px_rgba(103,232,249,.8)]" : ""} ${isRemoval || isTalkingHeadCut ? "border-dashed border-red-300/90 bg-red-500/35 text-red-100 hover:bg-red-500/60" : isSfx ? "border-amber-300/70 bg-amber-400/25 text-amber-100" : isBRoll ? "border-violet-400/70 bg-violet-500/50 text-violet-50" : "border-blue-400/70 bg-blue-500/50 text-blue-50"} ${optimisticPending ? "animate-pulse border-cyan-200 shadow-[0_0_14px_rgba(103,232,249,.65)]" : optimisticCompleted ? "border-emerald-300 shadow-[0_0_12px_rgba(110,231,183,.45)]" : optimisticFailed ? "border-rose-400" : ""}`}
                      style={{
                        left: 0,
                        width: "100%",
                        backgroundImage: isRemoval || isTalkingHeadCut ? "repeating-linear-gradient(-45deg, rgba(255,255,255,.13) 0 8px, transparent 8px 16px)" : undefined,
                      }}
                      title={isRemoval || isTalkingHeadCut ? `點擊審閱 AI 呈現建議：${clip.creator_hints?.[0] ?? clip.reason}` : "保留片段"}
                    >
                      {optimisticPending ? `${optimisticLabel} · 預覽已先套用` : optimisticCompleted ? `${optimisticLabel} · 已完成` : optimisticFailed ? `${optimisticLabel} · 可重試` : isRemoval ? `建議裁切${issueLabel ? ` · ${issueLabel}` : ""}` : isTalkingHeadCut ? "建議裁切 · 呈現狀態" : isSfx ? `SFX · ${clip.kind ?? "effect"}` : isSemanticStockBRoll ? "AI 語意 B-Roll · 靜音" : isBRoll ? "B-Roll · 靜音" : clip.creator_hints?.length || clip.review_flags?.length ? "保留 · 呈現提醒" : "保留"}
                      {isMusicClip && !canExtendMusic && <span aria-label="嘗試延長音樂" title="此音樂長度固定；開啟 AI 延長後才可拉伸" onPointerDown={(event) => beginConstrainedMusicStretch(event, clip.id)} className="absolute inset-y-0 right-0 w-2 cursor-ew-resize border-l border-white/35 transition-transform hover:bg-white/15" />}
                      {intentPeer && <span className="pointer-events-none absolute inset-0 rounded" style={{ backgroundColor: `${intentPeer.color}55`, boxShadow: `inset 0 0 0 1px ${intentPeer.color}` }}><span className="absolute left-1 top-1 rounded px-1 text-[9px] font-semibold text-white" style={{ backgroundColor: intentPeer.color }}>{intentPeer.name} 正在編輯</span></span>}
                    </button>
                    </motion.div>
                  );
                })}
                </AnimatePresence>
              </div>
              );
            })}
            {agentProposal && <AgentGhostTrack proposal={agentProposal} baseline={clips} zoom={zoom} />}
          </div>
          <RetentionHeatmapTrack prediction={prediction} duration={duration} zoom={zoom} />

          {retentionError && <p className="px-2 pb-2 text-xs text-red-300">留存預測失敗：{retentionError}</p>}

          <TransientSnapGuide zoom={zoom} />
          <TransientPlayhead zoom={zoom} />
          <CollaborationPresenceOverlay peers={collaboration.peers} layouts={collaborationLayouts} zoom={zoom} />

          {toolbar && <ContextualFloatingToolbar clip={toolbar.clip} anchor={toolbar} onExecute={(command, clip) => void executeEditorCommand(command, clip)} onClose={() => setToolbar(null)} />}

          {selectedClip && selectedClip.reviewStatus === "pending" && (
            <div className="absolute z-50" style={{ left: Math.min(selectedClip.displayStart * zoom, canvasWidth - 300), top: 42 }}>
              <CutReviewPopover
                clip={selectedClip}
                onClose={() => setSelectedClip(null)}
                onKeep={() => { keepClip(selectedClip.id); setSelectedClip(null); }}
                onConfirmCut={() => { confirmCut(selectedClip.id); triggerTimelineHaptic(16); setSelectedClip(null); }}
              />
            </div>
          )}
          {speakerHintClip && (
            <div className="absolute z-50" style={{ left: Math.min(speakerHintClip.displayStart * zoom, canvasWidth - 330), top: 42 }}>
              <SpeakerFeedbackPopover clip={speakerHintClip} onClose={() => setSpeakerHintClip(null)} />
            </div>
          )}
        </div>
      </div>
      <NudgeCommandBar timelineId={timelineId} userId={userId} />
      <div className="mt-2 flex justify-end"><JklShortcutHint /></div>
    </div>
      <AgenticAssistantPanel
        timelineId={timelineId}
        userId={userId}
        snapshot={sandboxSnapshot}
        selectedClipId={selectedClipId}
        onAccept={restoreSandboxSnapshot}
      />
      <OmnichannelExportCommandCenter timelineId={timelineId} userId={userId} />
      {timelineId && userId && <FinanceTrack timelineId={timelineId} userId={userId} />}
      {timelineId && userId && (
        <TTSNarrationPanel
          timelineId={timelineId}
          userId={userId}
          playheadTime={useTimelineStore.getState().playheadTime}
        />
      )}
      {timelineId && userId && <BeautyEnhancementPanel timelineId={timelineId} userId={userId} />}
      {timelineId && userId && <SemanticStockBRollPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} />}
      {projectId && userId && <AutoNarrativePanel projectId={projectId} userId={userId} sourceAssetIds={autoNarrativeAssetIds} />}
      {timelineId && userId && <VerticalDualLayoutPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} />}
      {timelineId && userId && <MemeGifPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} />}
      {timelineId && userId && <SmartAudioRemixToggle timelineId={timelineId} userId={userId} />}
      {timelineId && userId && <TextToMusicPanel timelineId={timelineId} userId={userId} />}
      {timelineId && userId && <AutoPipPanel timelineId={timelineId} userId={userId} mainAssetId={sourceAssetId} playheadTime={useTimelineStore.getState().playheadTime} />}
      {timelineId && userId && <SpatialTextPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} playheadTime={useTimelineStore.getState().playheadTime} />}
      {timelineId && userId && <VisualHooksPanel timelineId={timelineId} userId={userId} />}
      {timelineId && userId && <LongToShortsPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} />}
      {timelineId && userId && <TravelMapPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} playheadTime={useTimelineStore.getState().playheadTime} />}
      {timelineId && userId && <FitnessOverlayPanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} playheadTime={useTimelineStore.getState().playheadTime} />}
      {timelineId && userId && <TalkingHeadConfidencePanel timelineId={timelineId} userId={userId} sourceAssetId={sourceAssetId} />}
      {timelineId && userId && <AudioSyncPanel timelineId={timelineId} userId={userId} videoAssetId={sourceAssetId} playheadTime={useTimelineStore.getState().playheadTime} />}
      {showInspector && <ClipInspector clip={inspectedClip} timelineId={timelineId} userId={userId} />}
      <VersionComparisonDialog
        comparison={sandbox.comparison}
        nodes={sandbox.nodes}
        previewUrl={comparisonPreviewUrl}
        onClose={() => sandbox.setComparison(null)}
        onChoose={sandbox.checkout}
      />
      <EditorKeyboardManager onCommand={(command) => void executeEditorCommand(command)} onOpenPalette={() => setCommandPaletteOpen(true)} />
      <JklKeyboardManager />
      <EditorCommandPalette open={commandPaletteOpen} onOpenChange={setCommandPaletteOpen} onExecute={(command) => void executeEditorCommand(command)} />
    </section>
  );
}
