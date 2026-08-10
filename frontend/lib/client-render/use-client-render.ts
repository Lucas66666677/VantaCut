"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ClientTimelineRenderer } from "./ffmpeg-client-renderer";
import { decideRenderRoute, type RenderRoutingDecision } from "./hybrid-render-scheduler";
import type { ClientRenderProgress, ClientRenderRequest } from "@/types/client-render";
import { RenderFrameThumbnailer } from "./render-frame-thumbnailer";

const RECOVERY_KEY = "ai-video-editor:pending-client-render";

function saveRecoveryMarker(request: ClientRenderRequest, reason?: string) {
  // Files cannot be safely serialized to sessionStorage; retain only enough metadata to explain
  // why a reload/crash must fall back to the already-supported cloud-export path.
  sessionStorage.setItem(RECOVERY_KEY, JSON.stringify({ startedAt: Date.now(), sourceName: request.source.file.name, duration: request.estimatedDurationSeconds, reason }));
}

export function downloadRenderedVideo(blob: Blob, filename = "ai-video-export.mp4") {
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function useClientRender() {
  const rendererRef = useRef<ClientTimelineRenderer | null>(null);
  const [progress, setProgress] = useState<ClientRenderProgress | null>(null);
  const [decision, setDecision] = useState<RenderRoutingDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [thumbnail, setThumbnail] = useState<string | null>(null);

  useEffect(() => () => rendererRef.current?.dispose(), []);

  const exportTimeline = useCallback(async ({
    request,
    cloudFallback,
    filename,
  }: {
    request: ClientRenderRequest;
    cloudFallback: (reason: string) => Promise<void>;
    filename?: string;
  }): Promise<"client" | "cloud"> => {
    setError(null);
    const route = decideRenderRoute(request); setDecision(route);
    if (route.route === "cloud") { await cloudFallback(route.reason); return "cloud"; }
    rendererRef.current?.dispose(); rendererRef.current = new ClientTimelineRenderer();
    const thumbnailer = new RenderFrameThumbnailer(request.source.file);
    saveRecoveryMarker(request);
    try {
      const blob = await rendererRef.current.render(request, (next) => {
        setProgress(next);
        if (next.renderTimeSeconds === undefined || next.phase === "completed") return;
        void thumbnailer.sample(next.renderTimeSeconds).then((frame) => { if (frame) setThumbnail(frame); }).catch(() => undefined);
      });
      downloadRenderedVideo(blob, filename);
      sessionStorage.removeItem(RECOVERY_KEY);
      return "client";
    } catch (renderError) {
      const reason = renderError instanceof Error ? renderError.message : "Browser rendering failed.";
      setError(reason); saveRecoveryMarker(request, reason);
      // This handles OOM/codec/Worker termination without leaving the creator stranded.
      await cloudFallback(`本機導出失敗，已改用雲端：${reason}`);
      return "cloud";
    } finally {
      thumbnailer.dispose();
      rendererRef.current?.dispose(); rendererRef.current = null;
    }
  }, []);

  return { exportTimeline, progress, thumbnail, decision, error, recoverablePendingRender: typeof window !== "undefined" ? sessionStorage.getItem(RECOVERY_KEY) : null };
}
