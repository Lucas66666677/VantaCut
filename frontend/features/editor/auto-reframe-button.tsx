"use client";

import { useState } from "react";

import { useProjectStatus } from "@/features/project-status/use-project-status";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface AutoReframeButtonProps {
  timelineId: string;
  projectId: string;
  userId: string;
  resolution?: "720p" | "1080p";
}

/** Consumer-facing one-click path: configure smooth subject tracking, then request a 9:16 render. */
export function AutoReframeButton({ timelineId, projectId, userId, resolution = "1080p" }: AutoReframeButtonProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = useProjectStatus(projectId);

  const convert = async () => {
    setSubmitting(true); setError(null);
    try {
      const configured = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/auto-reframe`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ detector_stride: 2, smoothing: .75, max_pan_speed_px_per_second: 720 }),
      });
      const configuration = await configured.json() as { detail?: string };
      if (!configured.ok) throw new Error(configuration.detail ?? "無法設定主角追蹤");
      const rendered = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/render`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, resolution, aspect_ratio: "9:16" }),
      });
      const render = await rendered.json() as { detail?: string };
      if (!rendered.ok) throw new Error(typeof render.detail === "string" ? render.detail : "無法建立直式導出任務");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "自動裁切失敗");
    } finally { setSubmitting(false); }
  };

  const progress = status?.stage === "auto_reframing" || status?.stage === "rendering" ? status.progress : 0;
  return (
    <div className="space-y-2">
      <button type="button" onClick={() => void convert()} disabled={submitting} className="rounded-md border border-fuchsia-300/70 bg-fuchsia-500/15 px-3 py-1.5 text-xs font-semibold text-fuchsia-100 disabled:opacity-50">
        {submitting ? "正在建立直式導出…" : "AI 一鍵轉 9:16"}
      </button>
      {progress > 0 && <div className="w-44"><div className="mb-1 flex justify-between text-[10px] text-zinc-400"><span>{status?.message ?? "主角跟拍導出中"}</span><span>{progress}%</span></div><div className="h-1.5 overflow-hidden rounded bg-zinc-800"><div className="h-full bg-fuchsia-400 transition-all" style={{ width: `${progress}%` }} /></div></div>}
      {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
    </div>
  );
}
