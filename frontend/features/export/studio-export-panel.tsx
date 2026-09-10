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
  clearRecentRender,
  readExportSession,
  readRecentRender,
  writeExportSession,
  writeRecentRender,
  type RecentRenderRecord,
} from "@/features/export/export-session-storage";
import { RecentRenderRecovery } from "@/features/export/recent-render-recovery";
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
  /**
   * When the current upload started, as a changing number.
   *
   * The panel needs this, and not just `assetId`, to tell two situations
   * apart that look identical once the upload has finished: a `media_ready`
   * published while this asset was being processed, and a `media_ready` left
   * over from a previous asset. The status stream carries no asset id (see
   * `app/core/progress.py`), so "arrived after this upload began" is the only
   * signal available -- and it is only available if the panel is told when
   * the upload began.
   */
  uploadStartedAt?: number;
}

/**
 * What is being waited on, and what has been seen for it.
 *
 * `assetId` is undefined between the upload starting and the complete
 * response landing. Readiness can arrive inside that window, which is the
 * race this structure exists to survive: `readySeen` records it, and the
 * transition happens once the id is known.
 */
interface MediaTracking {
  assetId?: string;
  /**
   * Identity of the status event already on screen when tracking began.
   * Anything matching it belongs to whatever came before, so it is not
   * readiness for this asset. Undefined means "accept anything", which is
   * correct for a restored session: its asset is the one the events describe.
   */
  sinceKey?: string;
  readySeen: boolean;
  failure?: string;
  progress: number;
  message: string;
}

/** A publish is uniquely identified by its own timestamp; re-deliveries of
 *  the same event (the SSE client reconnects and replays) repeat it. */
function statusIdentity(event: { stage: string; progress: number; status: string; updated_at?: string; job_id?: string | null } | undefined): string | undefined {
  if (!event) return undefined;
  return [event.stage, event.progress, event.status, event.updated_at ?? "", event.job_id ?? ""].join("|");
}

export function StudioExportPanel({ projectId, assetId, uploadStartedAt }: StudioExportPanelProps) {
  const userId = useAuthStore((state) => state.user?.id);
  const status = useProjectStatus(projectId);
  const [phase, setPhase] = useState<Phase>({ kind: "waiting-for-media" });
  const [resolution, setResolution] = useState<"720p" | "1080p">("720p");
  const [confirming, setConfirming] = useState(false);
  const [restored, setRestored] = useState(false);
  /** What the panel is currently waiting on, from a fresh upload or a
   *  restored record, so a second upload resets rather than silently
   *  exporting the first one. */
  const [tracking, setTracking] = useState<MediaTracking | undefined>(undefined);
  /** Latest status without making every consumer re-run on it; read only at
   *  the instant an upload begins, to snapshot what came before. */
  const latestStatus = useRef(status);
  latestStatus.current = status;
  /** The upload-start value already acted on. Initialised to the mount-time
   *  prop so a remount does not replay an upload that began long ago. */
  const handledUploadStart = useRef<number | undefined>(uploadStartedAt);
  /**
   * Which asset the panel is currently about. Bumped whenever that changes.
   *
   * Every request started here captures the token first and drops its own
   * response if the token has moved on. Without it, a create or render answer
   * for the previous asset lands after the switch and re-points the panel at
   * it -- the same class of defect as the phase not resetting, arriving a
   * network round trip later.
   */
  const selection = useRef(0);
  /**
   * The most recent render a credit was spent on, whatever the panel is
   * pointing at now. Kept apart from the selection so switching assets
   * cannot take it away -- a warning about a lost export is not recovery.
   */
  const [recentRender, setRecentRender] = useState<RecentRenderRecord | undefined>(undefined);

  // Merges rather than replaces. Remounting the panel (applying a workspace
  // intent and coming back) re-runs the effects below with the same props; a
  // replacing write would then reduce a stored record holding a live
  // renderJobId to one holding only an assetId, losing a paid render.
  const remember = useCallback(
    (patch: Parameters<typeof writeExportSession>[2]) =>
      writeExportSession(userId, projectId, { ...(readExportSession(userId, projectId) ?? {}), ...patch }),
    [userId, projectId],
  );

  // Restore before anything else can overwrite it. Runs once per user/project.
  useEffect(() => {
    if (!userId || !projectId) return;
    const record = readExportSession(userId, projectId);
    setRecentRender(readRecentRender(userId, projectId));
    setRestored(true);
    if (!record) return;
    if (record.assetId && !record.timelineId && !record.renderJobId) {
      // A restored asset with nothing built on it yet. `sinceKey` is left
      // undefined on purpose: the events this project is publishing are about
      // this asset, including one that may already have arrived before this
      // effect ran, so there is nothing to exclude.
      setTracking({ assetId: record.assetId, readySeen: false, progress: 0, message: "正在處理素材" });
    }
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

  // An upload beginning supersedes whatever was being followed, and is the
  // moment the "everything before this belongs to something else" line is
  // drawn. Waiting for `assetId` to draw it would be too late: readiness can
  // be published before the complete response lands.
  useEffect(() => {
    // Only a *new* upload counts. The ref starts at the mount-time value, so
    // remounting with the same timestamp is not mistaken for another upload
    // -- which would re-snapshot the readiness already on screen as "before
    // this asset" and strand a ready asset on "正在處理素材".
    if (!uploadStartedAt || handledUploadStart.current === uploadStartedAt) return;
    handledUploadStart.current = uploadStartedAt;
    setConfirming(false);
    // A new upload supersedes anything built on the previous one, so the
    // token moves and every in-flight response for the old asset becomes
    // stale.
    selection.current += 1;
    // Reset what is on screen, not only what is tracked. Review found this
    // missing: `tracking` was reset while the phase was left alone, and the
    // tracking-to-phase effect deliberately refuses to touch the timeline,
    // queued and downloadable phases -- so uploading B after building A left
    // the export controls still pointing at A.
    // Only the selection is cleared. The recent-render record deliberately
    // survives: it is the one thing here that cost money.
    clearExportSession(userId, projectId);
    setPhase({ kind: "media-processing", progress: 0, message: "正在處理素材" });
    setTracking({
      sinceKey: statusIdentity(latestStatus.current),
      readySeen: false,
      progress: 0,
      message: "正在處理素材",
    });
  }, [uploadStartedAt, userId, projectId]);

  // The id arrives separately, and possibly after readiness already has.
  useEffect(() => {
    if (!assetId) return;
    setTracking((current) => (current && current.assetId !== assetId ? { ...current, assetId } : current));
    remember({ assetId });
  }, [assetId, remember]);

  // Status -> tracking. Depends on `tracking` as well as `status`, which is
  // the whole fix: when the asset id lands, this re-runs and can act on a
  // readiness that was already delivered. Every update returns the identical
  // object when nothing changed, so re-running cannot loop.
  useEffect(() => {
    if (!tracking || !status) return;
    const key = statusIdentity(status);
    // No key, or the same event that was already on screen when this upload
    // began: it describes something earlier, not this asset.
    if (!key || key === tracking.sinceKey) return;
    if (status.stage === "media_failed" || status.status === "failed") {
      const failure = status.message || "素材處理失敗";
      setTracking((current) => (current && current.failure !== failure ? { ...current, failure } : current));
      return;
    }
    if (status.stage === "media_ready") {
      setTracking((current) => (current && !current.readySeen ? { ...current, readySeen: true, progress: 100 } : current));
      return;
    }
    if (status.stage.startsWith("media_")) {
      const message = status.message || "正在處理素材";
      setTracking((current) =>
        current && (current.progress !== status.progress || current.message !== message)
          ? { ...current, progress: status.progress, message }
          : current,
      );
    }
  }, [status, tracking]);

  // Tracking -> phase. Only governs the pre-timeline phases; once a timeline
  // or a render exists, media stages are stale and must not overwrite it.
  useEffect(() => {
    if (!tracking) return;
    setPhase((current) => {
      if (
        current.kind !== "waiting-for-media" &&
        current.kind !== "media-processing" &&
        current.kind !== "media-failed" &&
        current.kind !== "ready"
      ) {
        return current;
      }
      if (tracking.failure) return { kind: "media-failed", message: tracking.failure };
      // Readiness needs the id too: the create call is made with it.
      if (tracking.readySeen && tracking.assetId) return { kind: "ready" };
      return { kind: "media-processing", progress: tracking.progress, message: tracking.message };
    });
  }, [tracking]);

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
    const token = selection.current;
    setPhase({ kind: "creating" });
    try {
      const timeline = await createTimelineFromAsset(projectId, sourceAssetId);
      // A different asset was chosen while this was in flight: this answer
      // describes the previous one, and applying it would re-point the panel
      // at an asset the user has moved on from.
      if (selection.current !== token) return;
      remember({ assetId: sourceAssetId, timelineId: timeline.id });
      setPhase({ kind: "timeline", timelineId: timeline.id });
    } catch (error) {
      if (selection.current !== token) return;
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "無法建立時間軸" });
    }
  };

  const startRender = async (timelineId: string) => {
    const token = selection.current;
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
        // Recorded before the token is consulted, and outside the selection
        // record. A late answer for the previous asset still describes a
        // credit that was spent, and dropping it here is exactly how the
        // render became unreachable.
        writeRecentRender(userId, projectId, receipt);
        setRecentRender(receipt);
        // Only now does the current selection matter. A late answer stops
        // here: B's state is not replaced, and the receipt above is already
        // safe.
        if (selection.current !== token) return;
        // Persisted before the first poll, so even an immediate reload finds
        // the job the credit was spent on.
        remember({ assetId: tracking?.assetId, timelineId, ...receipt });
        setPhase({ kind: "queued", receipt });
      } else if (selection.current !== token) {
        return;
      } else if (outcome.kind === "hydrating") {
        setPhase({ kind: "hydrating", message: outcome.message, estimatedReadyAt: outcome.estimatedReadyAt, timelineId });
      } else if (outcome.kind === "blocked") {
        setPhase({ kind: "blocked", message: outcome.message, timelineId });
      } else {
        setPhase({ kind: "error", message: outcome.message, timelineId });
      }
    } catch (error) {
      if (selection.current !== token) return;
      setPhase({ kind: "error", message: error instanceof Error ? error.message : "導出請求失敗", timelineId });
    }
  };

  /** Back to waiting, reusing the render already paid for. Never re-submits. */
  const resumePolling = (receipt: RenderReceipt) => setPhase({ kind: "queued", receipt });

  const startOver = () => {
    selection.current += 1;
    clearExportSession(userId, projectId);
    setTracking(undefined);
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
            <button type="button" onClick={() => void createTimeline(tracking?.assetId as string)} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
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

      {recentRender &&
        !(
          (phase.kind === "queued" || phase.kind === "downloadable" || phase.kind === "poll-stalled") &&
          phase.receipt.renderJobId === recentRender.renderJobId
        ) && (
          <RecentRenderRecovery
            record={recentRender}
            onDismiss={() => {
              clearRecentRender(userId, projectId);
              setRecentRender(undefined);
            }}
          />
        )}
    </section>
  );
}
