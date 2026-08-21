"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import type { ProjectStatusEvent } from "@/features/project-status/project-status-store";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import { useOptimisticProjectJobs } from "@/features/editor/use-optimistic-project-jobs";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const COPY = [
  "正在教 AI 怎麼聽懂你的笑話…",
  "正在把像素排成更有質感的隊形…",
  "正在替每一個畫面找最佳站位…",
  "即將完成，準備驚艷全場…",
];

export function empatheticProgressCopy(progress: number, stage?: string) {
  if (progress >= 92) return "最後檢查中，馬上就好…";
  if (stage?.includes("render") || stage?.includes("inpaint")) return COPY[1];
  if (stage?.includes("audio") || stage?.includes("studio")) return COPY[0];
  return COPY[Math.floor(progress / 25) % COPY.length];
}

/** Reusable non-blocking progress treatment for long AI or render operations. */
export function OptimisticProgress({ status, className = "" }: { status?: ProjectStatusEvent; className?: string }) {
  const [copyIndex, setCopyIndex] = useState(0);
  const progress = Math.max(0, Math.min(100, status?.progress ?? 0));
  useEffect(() => {
    if (status?.status !== "processing") return;
    const timer = window.setInterval(() => setCopyIndex((value) => (value + 1) % COPY.length), 3200);
    return () => window.clearInterval(timer);
  }, [status?.status]);
  const message = useMemo(() => {
    const playful = COPY[(copyIndex + Math.floor(progress / 20)) % COPY.length] ?? empatheticProgressCopy(progress, status?.stage);
    return status?.message ? `${playful} · ${status.message}` : playful;
  }, [copyIndex, progress, status?.message, status?.stage]);
  if (!status || status.status === "idle") return null;
  return <div aria-live="polite" className={`rounded-lg border border-violet-400/30 bg-violet-400/5 p-3 ${className}`}><div className="mb-2 flex items-center justify-between gap-3 text-xs"><span className="text-violet-100">{status.status === "failed" ? "這次卡住了，但不影響其他剪輯操作。" : message}</span><b className="shrink-0 text-violet-200">{progress}%</b></div><div className="h-1.5 overflow-hidden rounded-full bg-zinc-800"><div className={`h-full rounded-full transition-[width] duration-700 ease-out ${status.status === "failed" ? "bg-rose-400" : "bg-gradient-to-r from-violet-400 via-fuchsia-300 to-cyan-300"}`} style={{ width: `${status.status === "failed" ? 100 : progress}%` }} /></div></div>;
}

export function useDerivedPreviewUrl({ mediaAssetId, userId, taskId, completed }: { mediaAssetId?: string; userId?: string; taskId?: string; completed?: boolean }) {
  const [url, setUrl] = useState<string | undefined>();
  useEffect(() => {
    if (!mediaAssetId || !userId || !taskId || !completed) return;
    const controller = new AbortController();
    const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    void authenticatedFetch(`${base}/api/v1/media/${mediaAssetId}/derived-previews/${taskId}`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("derived preview unavailable")))
      .then((payload: { preview_url?: string | null }) => setUrl(payload.preview_url ?? undefined))
      .catch((error: unknown) => { if ((error as { name?: string }).name !== "AbortError") setUrl(undefined); });
    return () => controller.abort();
  }, [completed, mediaAssetId, taskId, userId]);
  return url;
}

/** Keep the proxy visible, show a shimmer while work runs, then crossfade the derived preview in. */
export function ProgressivePreviewSurface({ proxySrc, completedSrc, processing, className = "" }: { proxySrc?: string; completedSrc?: string; processing?: boolean; className?: string }) {
  // The SSE/WebSocket completion event can arrive before the signed HQ URL has decoded.
  // Keep the proxy on screen until the first decoded HQ frame is ready, so there is never a flash to black.
  const [completedReady, setCompletedReady] = useState(false);
  useEffect(() => { setCompletedReady(false); }, [completedSrc]);
  return <div className={`relative overflow-hidden bg-zinc-950 ${className}`}>
    {proxySrc ? <video muted playsInline autoPlay loop preload="metadata" src={proxySrc} className={`h-full w-full object-contain transition-[filter,opacity] duration-500 ${processing && !completedReady ? "opacity-95 blur-[0.45px]" : "opacity-100"}`} /> : <div className="absolute inset-0 animate-pulse bg-zinc-900" />}
    {processing && !completedReady && <div aria-label="AI 預覽正在準備" className="pointer-events-none absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent animate-[pulse_1.7s_ease-in-out_infinite]" />}
    <AnimatePresence>
      {completedSrc && <motion.video
        key={completedSrc}
        muted
        playsInline
        autoPlay
        loop
        preload="auto"
        src={completedSrc}
        onLoadedData={() => setCompletedReady(true)}
        initial={{ opacity: 0 }}
        animate={{ opacity: completedReady ? 1 : 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
        className="absolute inset-0 h-full w-full object-contain"
      />}
    </AnimatePresence>
    {processing && <span className="pointer-events-none absolute bottom-2 right-2 rounded-full border border-white/15 bg-black/35 px-2 py-1 text-[10px] text-white/80 backdrop-blur-md">先用 Proxy 繼續剪輯</span>}
  </div>;
}

/** Drop-in preview wrapper for a media canvas: proxy first, generated replacement only after SSE settles the job. */
export function OptimisticMediaPreview({ mediaAssetId, userId, projectId, proxySrc, className }: { mediaAssetId: string; userId: string; projectId?: string; proxySrc?: string; className?: string }) {
  useOptimisticProjectJobs(projectId);
  const jobs = useOptimisticEffectsStore((state) => state.jobs);
  const job = Object.values(jobs).filter((item) => item.mediaAssetId === mediaAssetId && ["matting", "inpainting"].includes(item.kind)).sort((left, right) => right.createdAt - left.createdAt)[0];
  const completedSrc = useDerivedPreviewUrl({ mediaAssetId, userId, taskId: job?.taskId, completed: job?.state === "completed" });
  return <ProgressivePreviewSurface proxySrc={proxySrc} completedSrc={completedSrc} processing={job?.state === "optimistic" || job?.state === "processing"} className={className} />;
}
