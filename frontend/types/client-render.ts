import type { TimelineClipInput } from "@/types/timeline";

export type ClientRenderResolution = "720p" | "1080p" | "4k";
export type ClientRenderAspectRatio = "16:9" | "9:16";

export interface ClientRenderEffects {
  hasAiGeneration?: boolean;
  hasDepthOr3d?: boolean;
  hasOpticalFlow?: boolean;
  hasHeavyColorPipeline?: boolean;
}

export interface ClientRenderSource {
  id: string;
  file: File;
}

export interface ClientRenderRequest {
  source: ClientRenderSource;
  bRollSources?: ClientRenderSource[];
  timeline: { segments?: TimelineClipInput[]; tracks?: Array<{ type: string; clips: TimelineClipInput[] }> };
  resolution: ClientRenderResolution;
  aspectRatio: ClientRenderAspectRatio;
  estimatedDurationSeconds: number;
  effects?: ClientRenderEffects;
}

export interface ClientRenderProgress {
  phase: "loading" | "rendering_chunk" | "concatenating" | "completed" | "failed";
  progress: number;
  chunkIndex?: number;
  chunkCount?: number;
  /** FFmpeg-reported/estimated output timeline timestamp for a low-rate live preview. */
  renderTimeSeconds?: number;
  runtime?: "single-thread" | "multi-thread";
}
