"use client";

import { useCallback, useEffect, useState } from "react";

import type { RetentionPrediction } from "@/types/retention";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function useRetentionPrediction(timelineId?: string, userId?: string) {
  const [prediction, setPrediction] = useState<RetentionPrediction>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  const refresh = useCallback(async () => {
    if (!timelineId || !userId) return;
    setLoading(true); setError(undefined);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/analysis/timelines/${timelineId}/retention-prediction`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh: true }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "無法更新留存預測");
      setPrediction(await response.json() as RetentionPrediction);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法更新留存預測");
    } finally {
      setLoading(false);
    }
  }, [timelineId, userId]);

  useEffect(() => { void refresh(); }, [refresh]);
  return { prediction, loading, error, refresh };
}
