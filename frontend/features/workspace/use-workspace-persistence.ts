"use client";

import { useEffect, useState } from "react";

import { loadWorkspaceLayout, saveWorkspaceLayout } from "@/lib/workspace/workspace-api";
import { useWorkspaceStore } from "@/features/workspace/workspace-store";

export function useWorkspacePersistence(projectId?: string, userId?: string) {
  const hydrated = useWorkspaceStore((state) => state.hydrated);
  const dirty = useWorkspaceStore((state) => state.dirty);
  const hydrate = useWorkspaceStore((state) => state.hydrate);
  const snapshot = useWorkspaceStore((state) => state.snapshot);
  const markPersisted = useWorkspaceStore((state) => state.markPersisted);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !userId || hydrated) return;
    let active = true;
    loadWorkspaceLayout(projectId)
      .then((layout) => { if (active) hydrate(layout ?? snapshot()); })
      .catch((reason: unknown) => { if (active) { hydrate(snapshot()); setError(reason instanceof Error ? reason.message : "工作區偏好讀取失敗"); } });
    return () => { active = false; };
  }, [hydrated, hydrate, projectId, snapshot, userId]);

  useEffect(() => {
    if (!projectId || !userId || !hydrated || !dirty) return;
    const timer = window.setTimeout(() => {
      saveWorkspaceLayout(projectId, snapshot())
        .then(markPersisted)
        .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "工作區偏好儲存失敗"));
    }, 650);
    return () => window.clearTimeout(timer);
  }, [dirty, hydrated, markPersisted, projectId, snapshot, userId]);

  return { error };
}
