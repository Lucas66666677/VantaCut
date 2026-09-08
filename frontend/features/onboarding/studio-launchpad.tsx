"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { AdaptiveEditorWorkspace } from "@/features/workspace/adaptive-editor-workspace";
import { useStudioProject } from "@/features/onboarding/use-studio-project";
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
  // Without this the workspace has no project, so `LocalMediaBin` keeps every
  // file in the browser while the surrounding copy promises background cloud
  // sync. It resolves to `undefined` until the first response and whenever
  // provisioning fails, which is the local-only state the bin already renders.
  const { projectId, status } = useStudioProject();
  if (status === "idle" || status === "loading") {
    // Do not expose the media picker during this window. A user can select a
    // file faster than a cold backend can provision the project; LocalMediaBin
    // intentionally keeps such a file local and does not retry it later.
    return <main aria-busy="true" className="grid min-h-screen place-items-center bg-[var(--lr-color-background)] text-sm text-[var(--lr-color-text-muted)]">正在準備雲端工作區…</main>;
  }
  return <AdaptiveEditorWorkspace timeline={timeline} projectId={projectId} projectStatus={status} />;
}
