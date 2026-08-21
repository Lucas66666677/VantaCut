"use client";

import { useEffect, useState } from "react";

import { InteractiveSankey } from "@/features/interactive/interactive-sankey";
import type { InteractiveAnalytics } from "@/types/interactive";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function InteractiveAnalyticsDashboard({ timelineId }: { timelineId: string; userId: string }) {
  const [analytics, setAnalytics] = useState<InteractiveAnalytics>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    void authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/interactive-analytics`)
      .then(async (response) => { if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "無法載入互動分析"); return response.json() as Promise<InteractiveAnalytics>; })
      .then(setAnalytics).catch((cause) => setError(cause instanceof Error ? cause.message : "無法載入互動分析"));
  }, [timelineId]);
  if (error) return <p className="text-sm text-red-300">{error}</p>;
  if (!analytics) return <p className="text-sm text-zinc-400">正在載入互動分析…</p>;
  return <InteractiveSankey analytics={analytics} />;
}
