"use client";

import { useCallback } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import type { TimelineClipInput } from "@/types/timeline";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Fetches the AI result and loads its keep/remove partitions directly into the review timeline. */
export function useRoughCutSuggestions(mediaAssetId: string | null, userId: string | null) {
  const loadTimeline = useTimelineStore((state) => state.loadTimeline);
  return useCallback(async () => {
    if (!mediaAssetId || !userId) throw new Error("需要素材與使用者識別才能讀取粗剪建議");
    const url = `${API_URL}/api/v1/analysis/rough-cut/${mediaAssetId}`;
    const response = await authenticatedFetch(url);
    const payload = await response.json() as { detail?: string; timeline_suggestions?: TimelineClipInput[] };
    if (!response.ok || !payload.timeline_suggestions) throw new Error(payload.detail ?? "尚未取得粗剪分析結果");
    loadTimeline(payload.timeline_suggestions);
    return payload.timeline_suggestions;
  }, [loadTimeline, mediaAssetId, userId]);
}
