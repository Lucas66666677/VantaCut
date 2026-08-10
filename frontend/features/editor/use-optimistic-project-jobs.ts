"use client";

import { useEffect } from "react";

import { useProjectStatus } from "@/features/project-status/use-project-status";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";

/** Settles optimistic operations from the existing project SSE/WebSocket feed. */
export function useOptimisticProjectJobs(projectId?: string) {
  const status = useProjectStatus(projectId, "websocket");
  const settleTask = useOptimisticEffectsStore((state) => state.settleTask);
  useEffect(() => {
    if (!status?.job_id) return;
    const state = status.status === "failed" ? "failed" : status.status === "completed" ? "completed" : "processing";
    settleTask(status.job_id, state, status.message);
  }, [settleTask, status?.job_id, status?.message, status?.status]);
  return status;
}
