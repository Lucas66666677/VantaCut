"use client";

import { useCallback, useEffect } from "react";

import { type OneClickTemplate, useOneClickStore } from "@/features/one-click/one-click-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface GenerateOptions {
  templateId: string;
  mediaAssetIds: string[];
  bgmAssetId?: string;
  resolution?: "720p" | "1080p";
  autoRender?: boolean;
}

export function useOneClickGenerate(projectId: string | null, userId: string | null) {
  const setTemplates = useOneClickStore((state) => state.setTemplates);
  const start = useOneClickStore((state) => state.start);
  const fail = useOneClickStore((state) => state.fail);
  const finish = useOneClickStore((state) => state.finish);

  useEffect(() => {
    let active = true;
    void fetch(`${API_URL}/api/v1/projects/one-click/templates`)
      .then(async (response) => {
        if (!response.ok) throw new Error("無法載入一鍵成片模板");
        return response.json() as Promise<OneClickTemplate[]>;
      })
      .then((templates) => { if (active) setTemplates(templates); })
      .catch((error: unknown) => { if (active) fail(error instanceof Error ? error.message : "無法載入模板"); });
    return () => { active = false; };
  }, [fail, setTemplates]);

  const generate = useCallback(async (options: GenerateOptions) => {
    if (!projectId || !userId) throw new Error("需要 projectId 與 userId 才能生成影片");
    if (!options.mediaAssetIds.length) throw new Error("請至少選擇一段已處理完成的影片素材");
    start();
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/one-click/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, template_id: options.templateId, media_asset_ids: options.mediaAssetIds, bgm_asset_id: options.bgmAssetId, resolution: options.resolution ?? "1080p", auto_render: options.autoRender ?? true }),
      });
      const payload = await response.json() as { task_id?: string; detail?: string };
      if (!response.ok || !payload.task_id) throw new Error(payload.detail ?? "無法建立一鍵成片任務");
      return payload.task_id;
    } catch (error) {
      const message = error instanceof Error ? error.message : "一鍵成片失敗";
      fail(message); throw error;
    } finally {
      finish();
    }
  }, [fail, finish, projectId, start, userId]);

  return { generate };
}
