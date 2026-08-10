export type TransitionKind = "crossfade" | "glitch" | "rgb_split" | "zoom_blur" | "depth_person_through" | "depth_background_peel" | "morph_cut";

export interface TransitionSpec {
  id: string;
  from_clip_id: string;
  to_clip_id: string;
  kind: TransitionKind;
  duration_seconds: number;
  shader_id?: string;
  fallback_xfade?: string;
}
