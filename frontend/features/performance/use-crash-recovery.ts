"use client";

import { useEffect, useRef, useState } from "react";

import { readCrashSnapshot, saveCrashSnapshot } from "@/features/performance/crash-recovery-db";
import { useTimelineStore } from "@/features/editor/timeline-store";

const WRITE_THROTTLE_MS = 75;

/** Local, high-frequency complement to the 30-second cloud draft checkpoint. */
export function useCrashRecovery(timelineId?: string) {
  const restoreCloudDraft = useTimelineStore((state) => state.restoreCloudDraft);
  const [status, setStatus] = useState<"idle" | "restoring" | "recovered" | "ready" | "error">(timelineId ? "restoring" : "idle");
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef<ReturnType<typeof useTimelineStore.getState> | null>(null);

  useEffect(() => {
    if (!timelineId || typeof indexedDB === "undefined") return;
    let disposed = false;
    setStatus("restoring");
    void (async () => {
      try {
        const snapshot = await readCrashSnapshot(timelineId);
        if (!disposed && snapshot) {
          restoreCloudDraft(snapshot.timeline, snapshot.editorState);
          setStatus("recovered");
        } else if (!disposed) setStatus("ready");
      } catch { if (!disposed) setStatus("error"); }
    })();

    const persist = (allowDisposed = false) => {
      if (!latest.current || (disposed && !allowDisposed)) return;
      const state = latest.current;
      void saveCrashSnapshot({
        timelineId, savedAtMs: Date.now(),
        timeline: { clips: state.clips, clip_animations: state.clipAnimations, speed_curves: state.speedCurves },
        editorState: { zoom: state.zoom, playhead_time: state.playheadTime },
      }).catch(() => { if (!disposed) setStatus("error"); });
    };
    const unsubscribe = useTimelineStore.subscribe((state) => {
      latest.current = state;
      if (timer.current !== undefined) return;
      timer.current = window.setTimeout(() => { timer.current = undefined; persist(); }, WRITE_THROTTLE_MS);
    });
    // Request durable quota where the browser permits it; denial is non-fatal.
    void navigator.storage?.persist?.();
    return () => { unsubscribe(); if (timer.current !== undefined) window.clearTimeout(timer.current); persist(true); disposed = true; };
  }, [restoreCloudDraft, timelineId]);

  return { status };
}
