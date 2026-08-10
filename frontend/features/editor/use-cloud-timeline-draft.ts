"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useTimelineStore, type CloudDraftEditorState, type CloudDraftTimeline } from "@/features/editor/timeline-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BACKUP_INTERVAL_MS = 30_000;

interface CloudDraftResponse { timeline: CloudDraftTimeline; editor_state: CloudDraftEditorState; updated_at?: string; detail?: string; }

/** Restores once, then saves the Zustand timeline every 30 seconds and before a tab is hidden. */
export function useCloudTimelineDraft(timelineId?: string, userId?: string, restoreFromServer = true) {
  const restoreCloudDraft = useTimelineStore((state) => state.restoreCloudDraft);
  const [status, setStatus] = useState<"idle" | "loading" | "saved" | "error">("idle");
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const ready = useRef(false);

  const payload = useCallback(() => {
    const state = useTimelineStore.getState();
    return {
      user_id: userId,
      timeline: { clips: state.clips, clip_animations: state.clipAnimations, speed_curves: state.speedCurves },
      editor_state: { zoom: state.zoom, playhead_time: state.playheadTime },
      client_updated_at: new Date().toISOString(),
    };
  }, [userId]);

  const save = useCallback(async (keepalive = false) => {
    if (!timelineId || !userId || !ready.current) return;
    try {
      const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/cloud-draft`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()), keepalive,
      });
      const result = await response.json() as CloudDraftResponse;
      if (!response.ok) throw new Error(result.detail ?? "草稿同步失敗");
      setStatus("saved"); setLastSavedAt(result.updated_at ?? new Date().toISOString());
    } catch { setStatus("error"); }
  }, [payload, timelineId, userId]);

  useEffect(() => {
    if (!timelineId || !userId) return;
    if (!restoreFromServer) { ready.current = true; setStatus("idle"); return; }
    let cancelled = false; setStatus("loading"); ready.current = false;
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/cloud-draft?user_id=${encodeURIComponent(userId)}`);
        if (response.status === 404) { if (!cancelled) setStatus("idle"); return; }
        const result = await response.json() as CloudDraftResponse;
        if (!response.ok) throw new Error(result.detail ?? "草稿讀取失敗");
        if (!cancelled) { restoreCloudDraft(result.timeline, result.editor_state); setLastSavedAt(result.updated_at ?? null); setStatus("saved"); }
      } catch { if (!cancelled) setStatus("error"); }
      finally { if (!cancelled) ready.current = true; }
    })();
    return () => { cancelled = true; ready.current = false; };
  }, [restoreCloudDraft, restoreFromServer, timelineId, userId]);

  useEffect(() => {
    if (!timelineId || !userId) return;
    const interval = window.setInterval(() => void save(), BACKUP_INTERVAL_MS);
    const onVisibilityChange = () => { if (document.visibilityState === "hidden") void save(true); };
    const onPageHide = () => void save(true);
    document.addEventListener("visibilitychange", onVisibilityChange); window.addEventListener("pagehide", onPageHide);
    return () => { window.clearInterval(interval); document.removeEventListener("visibilitychange", onVisibilityChange); window.removeEventListener("pagehide", onPageHide); };
  }, [save, timelineId, userId]);

  return { status, lastSavedAt, saveNow: () => save() };
}
