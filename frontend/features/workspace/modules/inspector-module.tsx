"use client";

import { ClipInspector } from "@/features/editor/clip-inspector";
import { useTimelineStore } from "@/features/editor/timeline-store";

export function InspectorWorkspaceModule({ timelineId }: { timelineId?: string }) {
  const selectedClipId = useTimelineStore((state) => state.selectedClipId);
  const clip = useTimelineStore((state) => state.clips.find((item) => item.id === selectedClipId) ?? null);
  return <ClipInspector clip={clip} timelineId={timelineId} />;
}
