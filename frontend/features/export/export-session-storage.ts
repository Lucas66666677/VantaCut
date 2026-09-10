"use client";

/**
 * What a reload has to survive for an export to be recoverable.
 *
 * Review found the hole: the panel told a user to refresh when polling gave
 * up, and refreshing destroyed the only copy of the asset, timeline and
 * render-job ids, because they lived in React state alone. The advice was not
 * merely unhelpful -- it threw away a render the user had already paid a
 * credit for, with no way to reach the finished file.
 *
 * ## Why sessionStorage
 *
 * The same choice `lib/auth/token-storage.ts` makes, for the same reason, and
 * here it is also a lifetime argument: resuming a job requires the bearer
 * token, and that token lives in `sessionStorage`. Persisting the job id
 * anywhere longer-lived would leave a record that outlives the credential
 * needed to act on it -- a resumable job nobody can resume, sitting in a
 * browser the next person may use.
 *
 * ## Why the key carries the user id
 *
 * A record is only ever read back for the account that wrote it. Without that,
 * signing out and in as someone else on the same browser would show them a
 * stranger's render job id -- which they could not fetch (the backend checks
 * `job.project.owner_id`), but which they should never have been offered.
 */

export interface ExportSessionRecord {
  assetId?: string;
  timelineId?: string;
  renderJobId?: string;
  /** Straight from the render response, so the credit outcome survives a
   *  reload instead of silently disappearing from the screen. */
  tier?: "free" | "pro";
  creditsRemaining?: number;
  watermarked?: boolean;
}

function storageKey(userId: string, projectId: string): string {
  return `vantacut_studio_export:${userId}:${projectId}`;
}

/** Every accessor is guarded: `sessionStorage` throws outright in some
 *  private-browsing and storage-disabled configurations, and losing recovery
 *  must never take the panel down with it. */
export function readExportSession(
  userId: string | undefined,
  projectId: string | undefined,
): ExportSessionRecord | undefined {
  if (!userId || !projectId || typeof window === "undefined") return undefined;
  try {
    const raw = window.sessionStorage.getItem(storageKey(userId, projectId));
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null) return undefined;
    return parsed as ExportSessionRecord;
  } catch {
    return undefined;
  }
}

export function writeExportSession(
  userId: string | undefined,
  projectId: string | undefined,
  record: ExportSessionRecord,
): void {
  if (!userId || !projectId || typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(storageKey(userId, projectId), JSON.stringify(record));
  } catch {
    // A panel that cannot remember is still a panel that works.
  }
}

export function clearExportSession(
  userId: string | undefined,
  projectId: string | undefined,
): void {
  if (!userId || !projectId || typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(storageKey(userId, projectId));
  } catch {
    // Nothing to do; the record expires with the session regardless.
  }
}

/**
 * The most recent render a credit was actually spent on, kept apart from the
 * current selection.
 *
 * Review's point: switching assets erases the selection record, and telling
 * someone their paid export is gone is not recovery. The job id has to
 * outlive the selection it was started from, so it lives under its own key
 * and is never cleared by an upload.
 *
 * One slot, deliberately. This is recovery for the export you just paid for,
 * not a render history -- a list would need its own UI, pruning and a story
 * about what belongs to whom.
 */
export interface RecentRenderRecord {
  renderJobId: string;
  tier: "free" | "pro";
  creditsRemaining: number;
  watermarked: boolean;
}

function recentKey(userId: string, projectId: string): string {
  return `vantacut_studio_export_recent:${userId}:${projectId}`;
}

export function readRecentRender(
  userId: string | undefined,
  projectId: string | undefined,
): RecentRenderRecord | undefined {
  if (!userId || !projectId || typeof window === "undefined") return undefined;
  try {
    const raw = window.sessionStorage.getItem(recentKey(userId, projectId));
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as RecentRenderRecord;
    return parsed && typeof parsed.renderJobId === "string" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

export function writeRecentRender(
  userId: string | undefined,
  projectId: string | undefined,
  record: RecentRenderRecord,
): void {
  if (!userId || !projectId || typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(recentKey(userId, projectId), JSON.stringify(record));
  } catch {
    // Recovery is best-effort; the panel still works without it.
  }
}

export function clearRecentRender(
  userId: string | undefined,
  projectId: string | undefined,
): void {
  if (!userId || !projectId || typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(recentKey(userId, projectId));
  } catch {
    // Nothing to do.
  }
}
