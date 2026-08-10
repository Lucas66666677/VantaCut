"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { AdaptiveEditorWorkspace } from "@/features/workspace/adaptive-editor-workspace";
import type { TimelineClipInput } from "@/types/timeline";

const sampleTimeline: TimelineClipInput[] = [
  { id: "sample-hook", source_start: 0, source_end: 3.6, action: "keep", confidence_score: 94, reason: "開場鉤子清楚，保留。" },
  { id: "sample-pause", source_start: 3.6, source_end: 5.1, action: "remove", confidence_score: 97, reason: "偵測到 1.5 秒靜音與贅詞。", issue_types: ["silence", "filler_word"] },
  { id: "sample-story", source_start: 5.1, source_end: 12.8, action: "keep", confidence_score: 88, reason: "敘事完整，適合加入動態字幕。" },
];

export function StudioLaunchpad() {
  const params = useSearchParams();
  const isDemo = params.get("mode") === "demo";
  const timeline = useMemo(() => isDemo ? sampleTimeline : [], [isDemo]);
  return <AdaptiveEditorWorkspace timeline={timeline} />;
}
