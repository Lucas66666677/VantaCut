"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ProjectStorageStatus {
  project_id: string;
  lifecycle_state: string;
  proxy_playback_available: boolean;
  high_quality_render_ready: boolean;
  active_hydration: {
    hydration_job_id: string | null;
    status: string;
    progress: number;
    estimated_ready_at: string | null;
    message: string;
  } | null;
}

export function useProjectStorage(projectId?: string, userId?: string) {
  const [storage, setStorage] = useState<ProjectStorageStatus>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  const refresh = useCallback(async () => {
    if (!projectId || !userId) return;
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/storage/status?user_id=${encodeURIComponent(userId)}`);
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "無法取得儲存狀態");
      setStorage(await response.json() as ProjectStorageStatus);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取得儲存狀態");
    }
  }, [projectId, userId]);

  const hydrate = useCallback(async () => {
    if (!projectId || !userId) return;
    setLoading(true); setError(undefined);
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/storage/hydrate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "無法啟動冷庫調回");
      const job = await response.json() as NonNullable<ProjectStorageStatus["active_hydration"]>;
      setStorage((current) => current ? { ...current, active_hydration: job, high_quality_render_ready: false } : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法啟動冷庫調回");
    } finally {
      setLoading(false);
    }
  }, [projectId, userId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!storage?.active_hydration || storage.active_hydration.status === "completed") return;
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh, storage?.active_hydration]);
  return { storage, loading, error, refresh, hydrate };
}
