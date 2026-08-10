import type { ClipLayout, TimelineClip, TrackType } from "@/types/timeline";

export const TRACK_ORDER: TrackType[] = ["finance_overlay", "b_roll", "multicam_video", "main_video", "audio_overlay"];

export function buildTrackLayouts(clips: TimelineClip[]): Map<TrackType, ClipLayout[]> {
  const tracks = new Map<TrackType, TimelineClip[]>();
  for (const track of TRACK_ORDER) tracks.set(track, []);
  for (const clip of clips) {
    // Mutate only the local build buffer: spreading here made loading n clips O(n²).
    const bucket = tracks.get(clip.track);
    if (bucket) bucket.push(clip); else tracks.set(clip.track, [clip]);
  }

  return new Map([...tracks.entries()].map(([track, trackClips]) => {
    let removedDuration = 0;
    const layout = [...trackClips]
      .sort((a, b) => (a.timeline_start ?? a.source_start) - (b.timeline_start ?? b.source_start))
      .map((clip) => {
        const duration = clip.source_end - clip.source_start;
        const isMainTrack = track === "main_video";
        // A Slip changes source in/out only. The explicit timeline position must
        // therefore remain authoritative instead of following source_start.
        const displayStart = isMainTrack
          ? (clip.timeline_start ?? clip.source_start) - removedDuration
          : (clip.timeline_start ?? clip.source_start);
        if (isMainTrack && clip.reviewStatus === "cut") removedDuration += duration;
        return { ...clip, displayStart, displayEnd: displayStart + duration };
      });
    return [track, layout];
  }));
}

export function visibleTimelineDuration(layouts: Map<TrackType, ClipLayout[]>): number {
  const mainTrack = layouts.get("main_video") ?? [];
  let duration = 10;
  // Avoid spreading a million values into Math.max, which exceeds browser call limits.
  for (const clip of mainTrack) if (clip.reviewStatus !== "cut") duration = Math.max(duration, clip.displayEnd);
  return duration;
}
