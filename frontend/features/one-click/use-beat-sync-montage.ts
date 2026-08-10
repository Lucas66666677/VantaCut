"use client";

import { useCallback, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface BeatSyncMontageOptions {
  bgmAssetId: string;
  mediaAssetIds: string[];
  aspectRatio?: "9:16" | "16:9";
  resolution?: "720p" | "1080p";
  autoRender?: boolean;
}

export function useBeatSyncMontage(projectId: string | null, userId: string | null) {
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = useCallback(async (options: BeatSyncMontageOptions) => {
    if (!projectId || !userId) throw new Error("需要 projectId 與 userId 才能建立卡點影片");
    if (options.mediaAssetIds.length < 10 || options.mediaAssetIds.length > 30) {
      throw new Error("請選擇 10 到 30 個已處理完成的照片或影片素材");
    }
    setIsGenerating(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/beat-sync/montage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          bgm_asset_id: options.bgmAssetId,
          media_asset_ids: options.mediaAssetIds,
          aspect_ratio: options.aspectRatio ?? "9:16",
          resolution: options.resolution ?? "1080p",
          auto_render: options.autoRender ?? true,
        }),
      });
      const payload = await response.json() as { task_id?: string; detail?: string };
      if (!response.ok || !payload.task_id) throw new Error(payload.detail ?? "無法建立一鍵卡點任務");
      return payload.task_id;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "一鍵卡點失敗";
      setError(message);
      throw cause;
    } finally {
      setIsGenerating(false);
    }
  }, [projectId, userId]);

  return { error, generate, isGenerating };
}
