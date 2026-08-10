"use client";

import { useCallback, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type AutoNarrativeTone = "funny_vlogger" | "emotional_vlogger";

export interface AutoNarrativeInput {
  mediaAssetIds: string[];
  bgmAssetId?: string;
  tone: AutoNarrativeTone;
  targetDurationSeconds: number;
  autoRender: boolean;
}

export function useAutoNarrative(projectId?: string, userId?: string) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const generate = useCallback(async (input: AutoNarrativeInput) => {
    if (!projectId || !userId) throw new Error("缺少專案或使用者資訊");
    if (input.mediaAssetIds.length < 5 || input.mediaAssetIds.length > 10) throw new Error("請選擇 5 至 10 段影片素材");
    setPending(true); setMessage(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/auto-narrative`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId, media_asset_ids: input.mediaAssetIds, bgm_asset_id: input.bgmAssetId || null,
          tone: input.tone, language: "zh", target_duration_seconds: input.targetDurationSeconds,
          resolution: "1080p", aspect_ratio: "9:16", auto_render: input.autoRender,
        }),
      });
      const payload = await response.json() as { task_id?: string; detail?: string };
      if (!response.ok || !payload.task_id) throw new Error(payload.detail ?? "無法建立 AI 旁白任務");
      setMessage("AI 正在看素材、寫旁白並組裝 Vlog；可在專案進度查看處理狀態。");
      return payload.task_id;
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : "無法建立 AI 旁白任務";
      setMessage(detail); throw cause;
    } finally { setPending(false); }
  }, [projectId, userId]);

  return { generate, pending, message };
}
