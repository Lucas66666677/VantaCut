export type ClipAction = "keep" | "remove";
export type ReviewStatus = "pending" | "kept" | "cut";
export type TrackType = "main_video" | "b_roll" | "audio_overlay" | "finance_overlay" | "multicam_video";

export interface SpeakerStateSummary {
  confidence_score: number;
  fluency_score: number;
  assessment_status?: "assessed" | "insufficient_visual_evidence";
  metrics?: Record<string, number>;
}

export interface AIModifiedProperty {
  original_value: unknown;
  current_value: unknown;
  source?: string;
}

export interface TimelineClipInput {
  id?: string;
  source_asset_id?: string;
  track?: TrackType;
  z_index?: number;
  audio_enabled?: boolean;
  audio_effects?: string[];
  timeline_start?: number;
  /** Original source duration from ffprobe; required for safe Slip boundary clamping. */
  source_duration?: number;
  source_start: number;
  source_end: number;
  action: ClipAction;
  confidence_score: number;
  reason: string;
  speaker_state?: SpeakerStateSummary | null;
  creator_hints?: string[];
  review_flags?: string[];
  issue_types?: Array<"silence" | "filler_word" | "repetition">;
  kind?: string;
  talking_head_recommendation?: "review_cut" | "b_roll";
  growing?: boolean;
  sequence_number?: number;
  camera_label?: string;
  proxy_key?: string;
  fade_in_seconds?: number;
  fade_out_seconds?: number;
  stock?: { provider: string; query?: string; pexels_url?: string; creator?: string | null; creator_url?: string | null };
  semantic_tags?: string[];
  analysis_types?: string[];
  audio_gain_db?: number;
  /** Non-destructive LUT metadata proposed by the editing agent or filter panel. */
  lut_key?: string;
  lut_intensity?: number;
  visual_adjustments?: { contrast?: number; brightness?: number; filter_intensity?: number; saturation?: number; exposure?: number };
  text_style?: { font_family?: string; color?: string; animation?: string };
  beat_sync_enabled?: boolean;
  ai_modified_properties?: Record<string, AIModifiedProperty>;
}

export interface TimelineClip extends TimelineClipInput {
  id: string;
  track: TrackType;
  z_index: number;
  audio_enabled: boolean;
  audio_effects: string[];
  reviewStatus: ReviewStatus;
}

export interface ClipLayout extends TimelineClip {
  displayStart: number;
  displayEnd: number;
}
