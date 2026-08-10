"use client";

import { create } from "zustand";

import type { TimelineClip } from "@/types/timeline";

export type TimelineEditTool = "select" | "slip" | "slide";
export type EditCursorContext = "idle" | "body" | "upper" | "lower" | "left-edge" | "right-edge";

export interface SlipGhostPreview {
  clipId: string;
  sourceAssetId?: string;
  inTimeMs: number;
  outTimeMs: number;
}

interface SlipSlideToolState {
  tool: TimelineEditTool;
  cursor: { x: number; y: number; context: EditCursorContext } | null;
  ghostPreview: SlipGhostPreview | null;
  setTool: (tool: TimelineEditTool) => void;
  setCursor: (cursor: SlipSlideToolState["cursor"]) => void;
  setGhostPreview: (preview: SlipGhostPreview | null) => void;
}

/** Intentionally transient: high-frequency drag feedback must never enter undo history. */
export const useSlipSlideToolStore = create<SlipSlideToolState>((set) => ({
  tool: "select",
  cursor: null,
  ghostPreview: null,
  setTool: (tool) => set({ tool, cursor: null, ghostPreview: null }),
  setCursor: (cursor) => set({ cursor }),
  setGhostPreview: (ghostPreview) => set({ ghostPreview }),
}));

export interface SlipResolution { sourceStart: number; sourceEnd: number; overshoot: number; atBoundary: boolean; }

/** Keeps timeline duration fixed while moving the media window inside its original source. */
export function resolveSlip(clip: TimelineClip, requestedDelta: number): SlipResolution {
  const duration = clip.source_end - clip.source_start;
  // source_duration comes from ffprobe. Falling back to source_end is deliberately safe:
  // it refuses to invent media outside a source whose duration has not arrived yet.
  const sourceDuration = Math.max(clip.source_end, clip.source_duration ?? clip.source_end);
  const minimum = -clip.source_start;
  const maximum = sourceDuration - clip.source_end;
  const applied = Math.max(minimum, Math.min(maximum, requestedDelta));
  return {
    sourceStart: Number((clip.source_start + applied).toFixed(3)),
    sourceEnd: Number((clip.source_start + applied + duration).toFixed(3)),
    overshoot: requestedDelta - applied,
    atBoundary: Math.abs(requestedDelta - applied) > .0001,
  };
}

export interface SlideResolution { delta: number; overshoot: number; atBoundary: boolean; }

/** A slide retimes the target while trimming its immediate neighbours, keeping the sequence duration stable. */
export function resolveSlide(previous: TimelineClip | undefined, next: TimelineClip | undefined, requestedDelta: number): SlideResolution {
  const minimumFrame = .04;
  const minimum = previous ? previous.source_start + minimumFrame - previous.source_end : 0;
  const maximum = next ? next.source_end - minimumFrame - next.source_start : 0;
  const applied = Math.max(minimum, Math.min(maximum, requestedDelta));
  return { delta: Number(applied.toFixed(3)), overshoot: requestedDelta - applied, atBoundary: Math.abs(requestedDelta - applied) > .0001 };
}

/** Compresses an impossible drag so the hand feels a physical bumper instead of a dead stop. */
export function rubberBandPixels(overshootSeconds: number, zoom: number): number {
  const distance = overshootSeconds * zoom;
  return Math.sign(distance) * 24 * (1 - Math.exp(-Math.abs(distance) / 24));
}

export function cursorGlyph(tool: TimelineEditTool, context: EditCursorContext): string {
  if (tool === "slip") return context === "left-edge" ? "⇤" : context === "right-edge" ? "⇥" : "⇆";
  if (tool === "slide") return context === "left-edge" ? "↤" : context === "right-edge" ? "↦" : "⇄";
  return "⌁";
}
