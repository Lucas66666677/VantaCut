"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createTimelineFromAsset,
  fetchRenderDownloadUrl,
  requestRender,
  type RenderOutcome,
} from "@/lib/api/studio-export";
import {
  clearExportSession,
  readExportSession,
  writeExportSession,
} from "@/features/export/export-session-storage";
import { useProjectStatus } from "@/features/project-status/use-project-status";
import { useAuthStore } from "@/lib/auth/auth-store";

/**
 * The missing last leg of the studio: upload -> timeline -> render -> download.
 *
 * Before this, the workspace had no export surface at all. `workspace-registry`
 * registers five modules -- timeline, inspector, colour wheels, scopes, audio
 * mixer -- and none of them exports; `ClientRenderExport` exists and is mounted
 * nowhere. So an uploaded video could be edited and never left the browser.
 *
 * Every call here is an endpoint that already exists. Nothing was added to the
 * backend for this panel.
 *
 * ## What it will not claim
 *
 * The credit balance is not shown before a render, because the app cannot know
 * it: `GET /auth/me` returns id, email, display name and is_active, and
 * nothing else. So the confirmation states the rule -- a free-plan render costs
 * one credit and carries a watermark -- and the authoritative tier, balance and
 * watermark flag are shown only after the render response reports them.
 *
 * ## Recovery, and why it is not optional
 *
 * A render costs a credit and can outlast the page. Review found the first
 * version telling users to refresh when polling gave up, while the asset,
 * timeline and render-job ids lived only in React state -- so refreshing threw
 * away the very result it promised to recover, and the user had already paid
 * for it.
 *
 * The ids are now kept per authenticated user and project (see
 * `export-session-storage.ts`) and restored on mount, so a reload resumes
 * polling the job that is already running. **Resuming never re-submits a
 * render.** Every path back into polling reuses the stored `renderJobId`; the
 * only call that spends a credit is the one behind the confirmation, and
 * `test_reload_resumes_without_submitting_another_render` pins that.
 *
 * Hydration and failure are dead ends without a way forward, so both offer an
 * explicit retry. They are deliberately different: retrying a poll is free and
 * goes straight back to waiting, while retrying after a cold-storage restore
 * has to spend another credit and therefore returns to the confirmation.
 */

const POLL_INTERVAL_MS = 5_000;
/** Bounded so a stuck job stops polling and offers a retry rather than
 *  spinning forever. Recovery makes this recoverable rather than terminal. */
const MAX_POLL_ATTEMPTS = 120;

interface RenderReceipt {
  renderJobId: string;
  tier: "free" | "pro";
  creditsRemaining: number;
  watermarked: boolean;
}

type Phase =
  | { kind: "waiting-for-media" }
  | { kind: "media-processing"; progress: number; message: string }
  | { kind: "media-failed"; message: string }
  | { kind: "ready" }
  | { kind: "creating" }
  | { kind: "timeline"; timelineId: string }
  | { kind: "requesting"; timelineId: string }
  | { kind: "queued"; receipt: RenderReceipt }
  | { kind: "downloadable"; url: string; receipt: RenderReceipt }
  | { kind: "poll-stalled"; receipt: RenderReceipt; message: string }
  | { kind: "hydrating"; message: string; estimatedReadyAt?: string; timelineId?: string }
  | { kind: "blocked"; message: string; timelineId?: string }
  | { kind: "error"; message: string; timelineId?: string };

interface StudioExportPanelProps {
  projectId?: string;
  /** The asset `LocalMediaBin` last finished uploading, if any. */
  assetId?: string;
}

export function StudioExportPanel({ projectId, assetId }: StudioExportPanelProps) {
  const userId = useAuthStore((state) => state.user?.id);
  const status = useProjectStatus(projectId);
  const [phase, setPhase] = useState<Phase>({ kind: "waiting-for-media" });
  const [resolution, setResolution] = useState<"720p" | "1080p">("720p");
  const [confirming, setConfirming] = useState(false);
  const [restored, setRestored] = useState(false);
  /** Which asset the panel is currently following, from either a fresh upload
   *  or a restored record, so a second upload resets rather than silently
   *  exporting the first one. */
  const trackedAsset = useRef<string | undefined>(undefined);

  const remember = useCallback(
    (record: Parameters<typeof writeExportSession>[2]) => writeExportSession(userId, projectId, record),
    [userId, projectId],
  );

  // Restore before anything else can overwrite it. Runs once per user/project.
  useEffect(() => {
    if (!userId || !projectId) return;
    const record = readExportSession(userId, projectId);
    setRestored(true);
    if (!record) return;
    trackedAsset.current = record.assetId;
    if (record.renderJobId && record.tier && typeof record.creditsRemaining === "number") {
      // Straight back into polling the job that is already running. No render
      // is submitted, so no second credit is spent.
      setPhase({
        kind: "queued",
        receipt: {
          renderJobId: record.renderJobId,
          tier: record.tier,
          creditsRemaining: record.creditsRemaining,
          watermarked: Boolean(record.watermarked),
        },
      });
    } else if (record.timelineId) {
      setPhase({ kind: "timeline", timelineId: record.timelineId });
    } else if (record.assetId) {
      setPhase({ kind: "media-processing", progress: 0, message: "正在處理素材" });
    }
  }, [userId, projectId]);

  // A newly uploaded asset supersedes whatever was being followed.
  useEffect(() => {
    if (!assetId || trackedAsset.current === assetId) return;
    trackedAsset.current = assetId;
    setConfirming(false);
    setPhase({ kind: "media-processing", progress: 0, message: "正在處理素材" });
    remember({ assetId });
  }, [assetId, remember]);

  // The worker's own progress, not a guess. Only advances the phase while the
  // panel is still waiting on media; once a timeline exists these stages are
  // stale and must not overwrite render state.
  useEffect(() => {
    if (!trackedAsset.current || !status) return;
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
  }, [status]);

  // Poll the only completion signal the backend exposes: download-url answers
  // 404 until the job is COMPLETED.
  useEffect(() => {
    if (phase.kind !== "queued") return;
    const { receipt } = phase;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const url = await fetchRenderDownloadUrl(receipt.renderJobId);
        if (cancelled) return;
        if (url) {
          setPhase({ kind: "downloadable", url, receipt });
          return;
        }
      } catch (error) {
        if (cancelled) return;
        // Transient by assumption: the job is still there, and retrying the
        // query costs nothing. The user is given that retry rather than a
        // dead end.
        setPhase({
          kind: "poll-stalled",
          receipt,
          message: error instanceof Error ? error.message : "無法查詢導出狀態",
        });
        return;
      }
      if (attempts >= MAX_POLL_ATTEMPTS) {
        setPhase({ kind: "poll-stalled", receipt, message: "導出仍在進行，這個頁面暫時停止查詢。" });
        return;
      }
      timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };
    // Asked immediately rather than after the first interval: a short render
    // can already be finished by the time the response lands, and a reload
    // resuming an old job should not wait five seconds to say so.
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

  const createTimeline = async (sourceAssetId: string) => {
    setPhase({ kind: "creating" });
    try {
      const timeline = await createTimelineFromAsset(projectId, sourceAssetId);
      remember({ assetId: sourceAssetId, timelineId: timeline.id });
      setPhase({ kind: "timeline", timelineId: timeline.id });
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "無法建立時間軸" });
    }
  };

  const startRender = async (timelineId: string) => {
    setConfirming(false);
    setPhase({ kind: "requesting", timelineId });
    try {
      const outcome: RenderOutcome = await requestRender(timelineId, resolution);
      if (outcome.kind === "queued") {
        const receipt: RenderReceipt = {
          renderJobId: outcome.renderJobId,
          tier: outcome.tier,
          creditsRemaining: outcome.creditsRemaining,
          watermarked: outcome.watermarked,
        };
        // Persisted before the first poll, so even an immediate reload finds
        // the job the credit was spent on.
        remember({ assetId: trackedAsset.current, timelineId, ...receipt });
        setPhase({ kind: "queued", receipt });
      } else if (outcome.kind === "hydrating") {
        setPhase({ kind: "hydrating", message: outcome.message, estimatedReadyAt: outcome.estimatedReadyAt, timelineId });
      } else if (outcome.kind === "blocked") {
        setPhase({ kind: "blocked", message: outcome.message, timelineId });
      } else {
        setPhase({ kind: "error", message: outcome.message, timelineId });
      }
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "導出請求失敗", timelineId });
    }
  };

  /** Back to waiting, reusing the render already paid for. Never re-submits. */
  const resumePolling = (receipt: RenderReceipt) => setPhase({ kind: "queued", receipt });

  const startOver = () => {
    clearExportSession(userId, projectId);
    trackedAsset.current = undefined;
    setConfirming(false);
    setPhase({ kind: "waiting-for-media" });
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

      <div className="mt-3 text-xs" data-testid="studio-export-state" data-restored={restored ? "1" : "0"}>
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
            <button type="button" onClick={() => void createTimeline(trackedAsset.current as string)} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
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

        {(phase.kind === "queued" || phase.kind === "downloadable" || phase.kind === "poll-stalled") && (
          <div className="space-y-2">
            <p role="status" className="text-[var(--lr-color-text-secondary)]">
              {phase.kind === "queued" ? "已排入渲染佇列，完成後會出現下載連結。" : phase.kind === "downloadable" ? "導出完成。" : phase.message}
              {" "}方案 {phase.receipt.tier === "free" ? "免費" : "Pro"}／剩餘點數 {phase.receipt.creditsRemaining}
              {phase.receipt.watermarked ? "／輸出含浮水印" : "／無浮水印"}
            </p>
            {phase.kind === "downloadable" && (
              <a href={phase.url} className="inline-block rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-secondary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
                下載影片
              </a>
            )}
            {phase.kind === "poll-stalled" && (
              <div className="space-y-2">
                {/* Free, and reuses the job already paid for. */}
                <button type="button" onClick={() => resumePolling(phase.receipt)} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
                  重新查詢導出狀態
                </button>
                <p className="text-[var(--lr-color-text-muted)]">這次查詢不會重新送出渲染，也不會再扣點數。重新整理頁面也會回到這個進度。</p>
              </div>
            )}
          </div>
        )}

        {phase.kind === "hydrating" && (
          <div className="space-y-2">
            <p role="status" className="text-[var(--lr-color-warning)]">
              {phase.message}
              {phase.estimatedReadyAt ? `（預計 ${new Date(phase.estimatedReadyAt).toLocaleString()} 可用）` : ""}
              。素材回到熱儲存後請再導出一次。
            </p>
            {phase.timelineId && (
              // Back to the confirmation rather than straight to a render:
              // this one does spend a credit, so it must be asked for again.
              <button type="button" onClick={() => { setPhase({ kind: "timeline", timelineId: phase.timelineId as string }); setConfirming(true); }} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border-strong)] px-3 py-2 text-xs font-semibold">
                再試一次導出
              </button>
            )}
          </div>
        )}

        {phase.kind === "blocked" && (
          <p role="alert" className="text-[var(--lr-color-warning)]">方案限制：{phase.message}</p>
        )}

        {phase.kind === "error" && (
          <div className="space-y-2">
            <p role="alert" className="text-[var(--lr-color-error)]">導出失敗：{phase.message}</p>
            {phase.timelineId && (
              <button type="button" onClick={() => { setPhase({ kind: "timeline", timelineId: phase.timelineId as string }); setConfirming(true); }} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border-strong)] px-3 py-2 text-xs font-semibold">
                再試一次導出
              </button>
            )}
            <button type="button" onClick={startOver} className="ml-2 rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] px-3 py-2 text-xs">
              重新開始
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
