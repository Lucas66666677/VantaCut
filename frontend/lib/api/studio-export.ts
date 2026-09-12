"use client";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

/**
 * The three authenticated calls between an uploaded asset and a downloadable
 * render. No new backend surface: `POST /projects/{id}/timelines`,
 * `POST /timelines/{id}/render` and
 * `GET /timelines/render-jobs/{id}/download-url` all already exist.
 *
 * Kept apart from the panel so the response handling -- which is where the
 * backend's shapes are genuinely awkward -- can be read on its own.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface CreatedTimeline {
  id: string;
  version: number;
  is_current: boolean;
}

export type RenderOutcome =
  | { kind: "queued"; renderJobId: string; creditsRemaining: number; tier: "free" | "pro"; watermarked: boolean }
  /** 1080p/4K on an asset that has aged into Deep Archive. Not a failure: the
   *  backend has started a restore and the render must be asked for again
   *  later, so the panel says hours rather than showing a spinner. */
  | { kind: "hydrating"; message: string; estimatedReadyAt?: string }
  /** Out of render credits (402) or outside the plan's entitlement (403).
   *  Distinct from a failure: nothing is wrong, and retrying unchanged will
   *  not help. */
  | { kind: "blocked"; message: string }
  | { kind: "failed"; message: string };

/** FastAPI's `detail` is a string for most raises and an object for the
 *  cold-storage branch, so both shapes are read rather than assumed. */
function readDetail(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return undefined;
}

function coldStorage(body: unknown): { message: string; estimatedReadyAt?: string } | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return undefined;
  const record = detail as { code?: unknown; message?: unknown; estimated_ready_at?: unknown };
  if (record.code !== "cold_storage_hydration_started") return undefined;
  return {
    message: typeof record.message === "string" ? record.message : "正在從冷庫調回高畫質素材",
    estimatedReadyAt: typeof record.estimated_ready_at === "string" ? record.estimated_ready_at : undefined,
  };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

export async function createTimelineFromAsset(
  projectId: string,
  sourceAssetId: string,
  name = "第一版剪輯",
): Promise<CreatedTimeline> {
  const response = await authenticatedFetch(`${API_URL}/api/v1/projects/${projectId}/timelines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_asset_id: sourceAssetId, name }),
  });
  const body = await readJson(response);
  if (!response.ok) {
    // 409 means the asset is still being probed -- the caller retries rather
    // than changing anything, so the status is preserved in the message.
    throw new Error(readDetail(body) ?? `無法建立時間軸（${response.status}）`);
  }
  return body as CreatedTimeline;
}

export async function requestRender(
  timelineId: string,
  resolution: "720p" | "1080p",
): Promise<RenderOutcome> {
  const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution, aspect_ratio: "16:9" }),
  });
  const body = await readJson(response);

  // A queued render and a started cold-storage restore both answer 202: the
  // route's success status is 202, and the hydration branch raises
  // HTTPException(202). Only the body tells them apart, so the shape is
  // checked rather than the status.
  const hydration = coldStorage(body);
  if (hydration) return { kind: "hydrating", ...hydration };

  if (response.ok && body && typeof body === "object" && "render_job_id" in body) {
    const record = body as {
      render_job_id: string;
      render_credits_remaining: number;
      subscription_tier: "free" | "pro";
      watermark_applied: boolean;
    };
    return {
      kind: "queued",
      renderJobId: record.render_job_id,
      creditsRemaining: record.render_credits_remaining,
      tier: record.subscription_tier,
      watermarked: record.watermark_applied,
    };
  }

  if (response.status === 402 || response.status === 403) {
    return { kind: "blocked", message: readDetail(body) ?? "這個方案無法執行這次導出。" };
  }
  return { kind: "failed", message: readDetail(body) ?? `導出請求失敗（${response.status}）` };
}

/**
 * The completion signal. There is no render-job status route; `download-url`
 * answers 404 until the job is COMPLETED with an output key, which makes it
 * the only existing way to ask "is it done?" without adding backend surface.
 * `undefined` means "not yet", not "never".
 */
export async function fetchRenderDownloadUrl(renderJobId: string): Promise<string | undefined> {
  const response = await authenticatedFetch(
    `${API_URL}/api/v1/timelines/render-jobs/${renderJobId}/download-url`,
  );
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`無法取得下載連結（${response.status}）`);
  const body = await readJson(response);
  const url = (body as { download_url?: unknown } | undefined)?.download_url;
  return typeof url === "string" ? url : undefined;
}
