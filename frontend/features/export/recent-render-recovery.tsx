"use client";

import { useEffect, useState } from "react";

import { fetchRenderDownloadUrl } from "@/lib/api/studio-export";
import type { RecentRenderRecord } from "@/features/export/export-session-storage";

/**
 * Getting back to a render a credit was already spent on.
 *
 * Switching assets replaces the panel's selection, and the selection record
 * with it. Review's objection was that the previous version then only said so
 * -- "a warning after losing access is not recovery". This is the recovery:
 * the job id is kept under its own key (see `export-session-storage.ts`), so
 * it survives both the switch and a reload, and this block polls it to a
 * download.
 *
 * It never submits a render. The only call it makes is
 * `GET /timelines/render-jobs/{id}/download-url`, which is the same free
 * completion probe the main panel uses; there is no path from here to
 * spending another credit.
 */

const POLL_INTERVAL_MS = 5_000;
const MAX_POLL_ATTEMPTS = 120;

interface RecentRenderRecoveryProps {
  record: RecentRenderRecord;
  onDismiss: () => void;
}

export function RecentRenderRecovery({ record, onDismiss }: RecentRenderRecoveryProps) {
  const [url, setUrl] = useState<string | undefined>(undefined);
  const [problem, setProblem] = useState<string | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (url) return;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const found = await fetchRenderDownloadUrl(record.renderJobId);
        if (cancelled) return;
        if (found) {
          setUrl(found);
          return;
        }
        setProblem(undefined);
      } catch (error) {
        if (cancelled) return;
        setProblem(error instanceof Error ? error.message : "無法查詢先前的導出");
        return;
      }
      if (attempts >= MAX_POLL_ATTEMPTS) {
        setProblem("先前的導出仍在進行，這個頁面暫時停止查詢。");
        return;
      }
      timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };
    let timer = setTimeout(() => void tick(), 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [record.renderJobId, url, attempt]);

  return (
    <section
      aria-label="先前的導出"
      className="mt-3 border border-[var(--lr-color-border-strong)] bg-[var(--lr-color-surface-raised)] p-3 text-xs"
    >
      <p className="font-semibold">先前的導出</p>
      <p className="mt-1 text-[var(--lr-color-text-muted)]">
        這次導出的點數已經扣除，換素材不會退回，所以它仍然保留在這裡。
        {" "}方案 {record.tier === "free" ? "免費" : "Pro"}／剩餘點數 {record.creditsRemaining}
        {record.watermarked ? "／輸出含浮水印" : "／無浮水印"}
      </p>
      {url ? (
        <a href={url} className="mt-2 inline-block rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-secondary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)]">
          下載先前的導出
        </a>
      ) : (
        <p role="status" className="mt-2 text-[var(--lr-color-text-muted)]">
          {problem ?? "正在確認先前的導出是否完成…"}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        {!url && problem && (
          // Free: the same completion probe, never a new render.
          <button type="button" onClick={() => setAttempt((value) => value + 1)} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] px-3 py-1.5 text-xs">
            重新查詢
          </button>
        )}
        <button type="button" onClick={onDismiss} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] px-3 py-1.5 text-xs">
          不再追蹤
        </button>
      </div>
    </section>
  );
}
