"use client";

import { useEffect, useRef, useState } from "react";

import {
  createTimelineFromAsset,
  fetchRenderDownloadUrl,
  requestRender,
  type RenderOutcome,
} from "@/lib/api/studio-export";
import { useProjectStatus } from "@/features/project-status/use-project-status";

/**
 * The missing last leg of the studio: upload -> timeline -> render -> download.
 *
 * Before this, the workspace had no export surface at all. `workspace-registry`
 * registers five modules -- timeline, inspector, colour wheels, scopes, audio
 * mixer -- and none of them exports; `ClientRenderExport` exists and is mounted
 * nowhere. So an uploaded video could be edited and never left the browser.
 *
 * Every call here is an endpoint that already exists. Nothing new was added to
 * the backend for this panel.
 *
 * ## What it will not claim
 *
 * The credit balance is not shown before a render, because the app cannot know
 * it: `GET /auth/me` returns id, email, display name and is_active, and
 * nothing else. Inventing a number, or assuming the free tier, would be a
 * confident lie in the one place a user is deciding whether to spend
 * something. So the confirmation states the rule -- a free-plan render costs
 * one credit and carries a watermark -- and the *authoritative* tier, balance
 * and watermark flag are shown only after the render response reports them.
 *
 * ## Waiting is driven by the server, not a guess
 *
 * An uploaded video is PROCESSING until `process_new_media` has probed it, and
 * a timeline cannot be built before then because the duration is unknown. The
 * progress shown comes from the project status stream the backend already
 * publishes (`media_probing`, `media_ready`, `media_failed`), so the panel
 * reports what the worker actually said rather than a spinner that means
 * nothing.
 */

const POLL_INTERVAL_MS = 5_000;
/** Bounded so a stuck job stops polling and says so rather than forever. */
const MAX_POLL_ATTEMPTS = 120;

type Phase =
  | { kind: "no-project" }
  | { kind: "waiting-for-media" }
  | { kind: "media-processing"; progress: number; message: string }
  | { kind: "media-failed"; message: string }
  | { kind: "ready" }
  | { kind: "creating" }
  | { kind: "timeline"; timelineId: string }
  | { kind: "requesting"; timelineId: string }
  | { kind: "queued"; outcome: Extract<RenderOutcome, { kind: "queued" }> }
  | { kind: "downloadable"; url: string; outcome: Extract<RenderOutcome, { kind: "queued" }> }
  | { kind: "hydrating"; message: string; estimatedReadyAt?: string }
  | { kind: "blocked"; message: string }
  | { kind: "error"; message: string };

interface StudioExportPanelProps {
  projectId?: string;
  /** The asset `LocalMediaBin` last finished uploading, if any. */
  assetId?: string;
}

export function StudioExportPanel({ projectId, assetId }: StudioExportPanelProps) {
  const status = useProjectStatus(projectId);
  const [phase, setPhase] = useState<Phase>({ kind: "waiting-for-media" });
  const [resolution, setResolution] = useState<"720p" | "1080p">("720p");
  const [confirming, setConfirming] = useState(false);
  // Which asset the current phase belongs to, so uploading a second file
  // resets rather than silently exporting the first one.
  const trackedAsset = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (trackedAsset.current === assetId) return;
    trackedAsset.current = assetId;
    setConfirming(false);
    setPhase(assetId ? { kind: "media-processing", progress: 0, message: "正在處理素材" } : { kind: "waiting-for-media" });
  }, [assetId]);

  // The worker's own progress, not a guess. Only advances the phase while the
  // panel is still waiting on media; once a timeline exists these stages are
  // stale and must not overwrite render state.
  useEffect(() => {
    if (!assetId || !status) return;
    setPhase((current) => {
      if (current.kind !== "media-processing" && current.kind !== "media-failed") return current;
      if (status.stage === "media_failed" || status.status === "failed") {
        return { kind: "media-failed", message: status.message || "素材處理失敗" };
      }
      if (status.stage === "media_ready") return { kind: "ready" };
      if (status.stage.startsWith("media_")) {
        return { kind: "media-processing", progress: status.progress, message: status.message || "正在處理素材" };
      }
      return current;
    });
  }, [assetId, status]);

  // Poll the only completion signal the backend exposes: download-url answers
  // 404 until the job is COMPLETED.
  useEffect(() => {
    if (phase.kind !== "queued") return;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const url = await fetchRenderDownloadUrl(phase.outcome.renderJobId);
        if (cancelled) return;
        if (url) {
          setPhase({ kind: "downloadable", url, outcome: phase.outcome });
          return;
        }
      } catch (error) {
        if (cancelled) return;
        setPhase({ kind: "error", message: error instanceof Error ? error.message : "無法查詢導出狀態" });
        return;
      }
      if (attempts >= MAX_POLL_ATTEMPTS) {
        setPhase({
          kind: "error",
          message: "導出仍在進行，但這個頁面已停止查詢。稍後重新整理即可看到結果。",
        });
        return;
      }
      timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };
    // Asked immediately rather than after the first interval: a short render
    // can already be finished by the time the response lands, and waiting five
    // seconds to discover that is five seconds of a spinner over a finished
    // file.
    let timer = setTimeout(() => void tick(), 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [phase]);

  if (!projectId) {
    return (
      <section aria-labelledby="studio-export-title" className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] p-4">
        <h2 id="studio-export-title" className="text-sm font-semibold">導出影片</h2>
        <p className="mt-1 text-xs text-[var(--lr-color-text-muted)]">雲端導出需要雲端工作區；目前素材只保存在此瀏覽器。</p>
      </section>
    );
  }

  const createTimeline = async () => {
    setPhase({ kind: "creating" });
    try {
      const timeline = await createTimelineFromAsset(projectId, assetId as string);
      setPhase({ kind: "timeline", timelineId: timeline.id });
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "無法建立時間軸" });
    }
  };

  const startRender = async (timelineId: string) => {
    setConfirming(false);
    setPhase({ kind: "requesting", timelineId });
    try {
      const outcome = await requestRender(timelineId, resolution);
      if (outcome.kind === "queued") setPhase({ kind: "queued", outcome });
      else if (outcome.kind === "hydrating") setPhase({ kind: "hydrating", message: outcome.message, estimatedReadyAt: outcome.estimatedReadyAt });
      else if (outcome.kind === "blocked") setPhase({ kind: "blocked", message: outcome.message });
      else setPhase({ kind: "error", message: outcome.message });
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "導出請求失敗" });
    }
  };

  return (
    <section aria-labelledby="studio-export-title" className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="studio-export-title" className="text-sm font-semibold">導出影片</h2>
          <p className="mt-1 text-xs text-[var(--lr-color-text-muted)]">上傳完成後，這裡會把素材整理成可導出的時間軸。</p>
        </div>
        <label className="text-xs text-[var(--lr-color-text-muted)]">
          <span className="mr-2">畫質</span>
          <select
            aria-label="導出畫質"
            value={resolution}
            onChange={(event) => setResolution(event.target.value as "720p" | "1080p")}
            className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-background)] px-2 py-1 text-xs"
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
        </label>
      </div>

      <div className="mt-3 text-xs" data-testid="studio-export-state">
        {phase.kind === "waiting-for-media" && (
          <p className="text-[var(--lr-color-text-muted)]">先加入一段影片，導出選項就會出現。</p>
        )}

        {phase.kind === "media-processing" && (
          <p role="status" className="text-[var(--lr-color-text-muted)]">
            {phase.message}（{Math.round(phase.progress)}%）。素材處理完成前無法導出。
          </p>
        )}

        {phase.kind === "media-failed" && (
          <p role="alert" className="text-[var(--lr-color-error)]">素材處理失敗：{phase.message}</p>
        )}

        {phase.kind === "ready" && (
          <div className="space-y-2">
            <p className="text-[var(--lr-color-text-muted)]">素材已處理完成，可以建立時間軸。</p>
            <button type="button" onClick={() => void createTimeline()} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
              建立時間軸
            </button>
          </div>
        )}

        {phase.kind === "creating" && <p role="status" className="text-[var(--lr-color-text-muted)]">正在建立時間軸…</p>}

        {phase.kind === "timeline" && !confirming && (
          <div className="space-y-2">
            <p className="text-[var(--lr-color-text-muted)]">時間軸已就緒。</p>
            <button type="button" onClick={() => setConfirming(true)} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
              導出影片
            </button>
          </div>
        )}

        {phase.kind === "timeline" && confirming && (
          // Deliberately does not show a balance: the app cannot read one.
          <div role="group" aria-label="確認導出" className="space-y-2 border border-[var(--lr-color-border-strong)] p-3">
            <p className="text-[var(--lr-color-text-secondary)]">
              這會送出一次雲端渲染（{resolution}）。免費方案每次導出會扣除 1 點渲染點數並加上浮水印；實際方案與剩餘點數會在送出後顯示。
            </p>
            <div className="flex gap-2">
              <button type="button" onClick={() => void startRender(phase.timelineId)} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
                確認並導出
              </button>
              <button type="button" onClick={() => setConfirming(false)} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] px-3 py-2 text-xs">
                取消
              </button>
            </div>
          </div>
        )}

        {phase.kind === "requesting" && <p role="status" className="text-[var(--lr-color-text-muted)]">正在送出導出請求…</p>}

        {(phase.kind === "queued" || phase.kind === "downloadable") && (
          <div className="space-y-2">
            <p role="status" className="text-[var(--lr-color-text-secondary)]">
              {phase.kind === "queued" ? "已排入渲染佇列，完成後會出現下載連結。" : "導出完成。"}
              {" "}方案 {phase.outcome.tier === "free" ? "免費" : "Pro"}／剩餘點數 {phase.outcome.creditsRemaining}
              {phase.outcome.watermarked ? "／輸出含浮水印" : "／無浮水印"}
            </p>
            {phase.kind === "downloadable" && (
              <a href={phase.url} className="inline-block rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-secondary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
                下載影片
              </a>
            )}
          </div>
        )}

        {phase.kind === "hydrating" && (
          <p role="status" className="text-[var(--lr-color-warning)]">
            {phase.message}
            {phase.estimatedReadyAt ? `（預計 ${new Date(phase.estimatedReadyAt).toLocaleString()} 可用）` : ""}
            。素材回到熱儲存後請再導出一次。
          </p>
        )}

        {/* Prefixed so a plan limit is not read as a malfunction. Mutation
            testing forced this: with the 402 branch deleted the message fell
            through to the failure branch and rendered identically, so the
            test could not tell the two apart. */}
        {phase.kind === "blocked" && <p role="alert" className="text-[var(--lr-color-warning)]">方案限制：{phase.message}</p>}

        {phase.kind === "error" && <p role="alert" className="text-[var(--lr-color-error)]">導出失敗：{phase.message}</p>}
      </div>
    </section>
  );
}
