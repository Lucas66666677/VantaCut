"use client";

import { TimelineEditor } from "@/features/editor/timeline-editor";
import type { TimelineClipInput } from "@/types/timeline";

export function TimelineWorkspaceModule({ timeline, timelineId, projectId, userId }: { timeline: TimelineClipInput[]; timelineId?: string; projectId?: string; userId?: string }) {
  return <TimelineEditor timeline={timeline} timelineId={timelineId} projectId={projectId} userId={userId} showInspector={false} />;
}
