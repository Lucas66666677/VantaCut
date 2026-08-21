"use client";

import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SemanticSnapType = "clip_edge" | "downbeat" | "speech_pause" | "action_peak";

export interface SemanticSnapPoint {
  id: string;
  time_seconds: number;
  type: SemanticSnapType;
  strength: number;
  label: string;
  source_asset_id?: string | null;
}

export type SemanticSnapPreferences = Record<Exclude<SemanticSnapType, "clip_edge">, boolean>;

export interface SnapResolution {
  time: number;
  point: SemanticSnapPoint | null;
  pull: number;
}

const DEFAULT_PREFERENCES: SemanticSnapPreferences = {
  downbeat: true,
  speech_pause: true,
  action_peak: true,
};

/**
 * Apply a soft magnetic field rather than abruptly changing pointer position.
 * The field is measured in pixels so the interaction feels constant at every zoom level.
 */
export function resolveSemanticSnap(
  rawTime: number,
  zoom: number,
  points: SemanticSnapPoint[],
  preferences: SemanticSnapPreferences,
): SnapResolution {
  // Keep the interaction target physically consistent across every zoom level.
  const radiusSeconds = 15 / Math.max(zoom, 1);
  const candidates = points
    .filter((point) => point.type === "clip_edge" || preferences[point.type as keyof SemanticSnapPreferences])
    .map((point) => ({ point, distance: Math.abs(rawTime - point.time_seconds) }))
    .filter((candidate) => candidate.distance <= radiusSeconds)
    .sort((left, right) => (left.distance / Math.max(left.point.strength, .2)) - (right.distance / Math.max(right.point.strength, .2)));
  const candidate = candidates[0];
  if (!candidate) return { time: Math.max(0, rawTime), point: null, pull: 0 };

  const proximity = 1 - candidate.distance / radiusSeconds;
  const hardLatch = candidate.distance <= 4 / Math.max(zoom, 1);
  const pull = hardLatch ? 1 : Math.min(.88, (.16 + proximity * proximity * .72) * Math.max(.55, candidate.point.strength));
  return {
    time: Math.max(0, hardLatch ? candidate.point.time_seconds : rawTime + (candidate.point.time_seconds - rawTime) * pull),
    point: candidate.point,
    pull,
  };
}

/** A small damped spring used on release; avoids introducing a motion library just for snapping. */
export function animateSnapSpring(from: number, to: number, onFrame: (time: number) => void): () => void {
  if (Math.abs(from - to) < .001) { onFrame(to); return () => undefined; }
  let frame = 0;
  let position = from;
  let velocity = 0;
  let last = performance.now();
  let cancelled = false;
  const tick = (now: number) => {
    if (cancelled) return;
    const delta = Math.min(.032, (now - last) / 1000);
    last = now;
    velocity = (velocity + (to - position) * 210 * delta) * .68;
    position += velocity * delta;
    if (Math.abs(to - position) < .002 && Math.abs(velocity) < .008) {
      onFrame(to);
      return;
    }
    onFrame(position);
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return () => { cancelled = true; cancelAnimationFrame(frame); };
}

export function triggerTimelineHaptic(milliseconds = 8) {
  if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") navigator.vibrate(milliseconds);
}

export function useSemanticSnapPoints(timelineId?: string, userId?: string) {
  const [points, setPoints] = useState<SemanticSnapPoint[]>([]);
  const [preferences, setPreferences] = useState<SemanticSnapPreferences>(DEFAULT_PREFERENCES);

  useEffect(() => {
    if (!timelineId || !userId) { setPoints([]); return; }
    const controller = new AbortController();
    void authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/semantic-snap-points`, { signal: controller.signal })
      .then(async (response) => response.ok ? response.json() : Promise.reject(new Error("semantic snap points unavailable")))
      .then((payload: { points?: SemanticSnapPoint[] }) => setPoints(Array.isArray(payload.points) ? payload.points : []))
      .catch((error: unknown) => { if ((error as { name?: string }).name !== "AbortError") setPoints([]); });
    return () => controller.abort();
  }, [timelineId, userId]);

  const toggle = useCallback((type: Exclude<SemanticSnapType, "clip_edge">) => {
    setPreferences((current) => ({ ...current, [type]: !current[type] }));
  }, []);
  return { points, preferences, toggle };
}

export function clipEdgeSnapPoints(clips: Array<{ id: string; track: string; source_start: number; source_end: number; timeline_start?: number }>): SemanticSnapPoint[] {
  return clips.flatMap((clip) => {
    const start = clip.track === "main_video" ? clip.source_start : (clip.timeline_start ?? clip.source_start);
    const end = start + (clip.source_end - clip.source_start);
    return [
      { id: `edge-${clip.id}-in`, time_seconds: start, type: "clip_edge" as const, strength: .58, label: "片段起點" },
      { id: `edge-${clip.id}-out`, time_seconds: end, type: "clip_edge" as const, strength: .58, label: "片段終點" },
    ];
  });
}
