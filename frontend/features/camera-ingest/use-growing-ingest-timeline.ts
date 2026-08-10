"use client";

import { useEffect } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import type { TimelineClipInput } from "@/types/timeline";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const INITIAL_RETRY_DELAY_MS = 1_000;
const MAX_RETRY_DELAY_MS = 15_000;

interface IngestEvent {
  extra?: {
    ingest?: {
      kind?: string;
      timeline_id?: string;
      clips?: TimelineClipInput[];
    };
  };
}

/** Subscribe only to the selected ingest timeline; EventSource reconnection is explicit for proxy failures. */
export function useGrowingIngestTimeline(projectId?: string, timelineId?: string): void {
  const upsertGrowingIngestClips = useTimelineStore((state) => state.upsertGrowingIngestClips);

  useEffect(() => {
    if (!projectId || !timelineId) return;
    let source: EventSource | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let retryDelay = INITIAL_RETRY_DELAY_MS;

    const connect = () => {
      if (disposed) return;
      source = new EventSource(`${API_URL}/api/v1/projects/${projectId}/status`);
      source.onopen = () => { retryDelay = INITIAL_RETRY_DELAY_MS; };
      source.addEventListener("status", (event: MessageEvent<string>) => {
        try {
          const payload = JSON.parse(event.data) as IngestEvent;
          const ingest = payload.extra?.ingest;
          if (ingest?.kind === "growing_timeline" && ingest.timeline_id === timelineId && Array.isArray(ingest.clips)) {
            upsertGrowingIngestClips(ingest.clips);
          }
        } catch {
          // Ignore a malformed progress message; the next full growing-timeline payload repairs state.
        }
      });
      source.onerror = () => {
        source?.close();
        if (disposed) return;
        retryTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(MAX_RETRY_DELAY_MS, retryDelay * 2);
      };
    };
    connect();
    return () => {
      disposed = true;
      source?.close();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [projectId, timelineId, upsertGrowingIngestClips]);
}
