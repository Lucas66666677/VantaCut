"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";

import { useEditorPerformanceStore } from "@/features/performance/editor-performance-store";
import { precacheKeyframeRange, subscribeMediaHoverIntent, useMediaRangeCacheServiceWorker, type MediaRangeIndex } from "@/features/editor/media-range-cache";
import { useKeyframeLivePreviewStore } from "@/features/editor/keyframe-live-preview-store";

export interface DemuxedVideoChunk { type: EncodedVideoChunkType; timestamp: number; duration?: number; data: ArrayBuffer; }
/** Must run in a dedicated worker. Do not call this contract from the UI thread. */
export interface VideoProxyDemuxer { chunksFromKeyframe(proxyUrl: string, range: { startUs: number; endUs: number; signal: AbortSignal }): AsyncIterable<DemuxedVideoChunk>; }
export interface ProxyVideoSource { assetId: string; proxyUrl: string; decoderConfig: VideoDecoderConfig; /** Generated with proxy metadata for exact I-frame byte prefetch. */ rangeIndex?: MediaRangeIndex; }
export interface CanvasPreviewClip {
  id: string;
  track: "main_video" | "b_roll" | "audio_overlay" | "multicam_video";
  sourceAssetId: string;
  sourceStartMs: number;
  sourceEndMs: number;
  timelineStartMs: number;
  zIndex: number;
  enabled?: boolean;
  opacity?: number;
  maskAssetId?: string;
  lutIntensity?: number;
}
export interface CanvasPreviewSubtitle {
  id: string;
  startMs: number;
  endMs: number;
  text: string;
  x?: number;
  y?: number;
  fontSize?: number;
  color?: string;
  strokeColor?: string;
}
export interface PreviewLut { dimension: number; /** RGBA Uint8 entries in WebGL 3D texture order. */ data: Uint8Array; }

interface Options { canvasRef: RefObject<HTMLCanvasElement | null>; sources: ProxyVideoSource[]; clips: CanvasPreviewClip[]; subtitles?: CanvasPreviewSubtitle[]; lut?: PreviewLut; /** URL of a worker-only MP4/WebM demuxer module exposing `demuxProxy`. */ workerDemuxerModuleUrl?: string; }
interface Player { isSupported: boolean; isReady: boolean; isBuffering: boolean; bufferingAssetIds: string[]; currentTimeMs: number; error: Error | null; seek: (timeMs: number) => Promise<void>; seekTo: (timeMs: number) => Promise<void>; play: (playbackRate?: number) => void; pause: () => void; subscribeTime: (listener: (timeMs: number) => void) => () => void; loadProxySegment: (assetId: string, startMs: number, endMs: number) => void; requestFrame: (assetId: string, timeMs: number) => Promise<ImageBitmap | null>; }

/**
 * UI-thread controller only. OffscreenCanvas, VideoDecoder, frame cache and all
 * matrix/compositing work live in `preview-compositor.worker.ts`; the UI merely
 * posts small timeline commands so dragging never waits on decoding.
 */
export function useVideoCanvasPlayer({ canvasRef, sources, clips, subtitles = [], lut, workerDemuxerModuleUrl }: Options): Player {
  const workerRef = useRef<Worker | null>(null); const animationRef = useRef<number | undefined>(undefined); const playing = useRef(false); const playbackRateRef = useRef(1); const timeRef = useRef(0); const timeListenersRef = useRef(new Set<(timeMs: number) => void>()); const lastTick = useRef<number | undefined>(undefined); const lastPrefetch = useRef<number>(-Infinity); const lastUiPublish = useRef<number>(-Infinity); const ghostRequestsRef = useRef(new Map<string, (bitmap: ImageBitmap | null) => void>());
  const [isReady, setReady] = useState(false); const [isBuffering, setBuffering] = useState(false); const [bufferingAssetIds, setBufferingAssetIds] = useState<string[]>([]); const [currentTimeMs, setCurrentTimeMs] = useState(0); const [error, setError] = useState<Error | null>(null);
  const { previewMaxHeight, antiAliasingEnabled, quality } = useEditorPerformanceStore();
  useMediaRangeCacheServiceWorker();
  const isSupported = typeof window !== "undefined" && "OffscreenCanvas" in window && "transferControlToOffscreen" in HTMLCanvasElement.prototype && "VideoDecoder" in window;
  const sourceKey = useMemo(() => JSON.stringify(sources.map(({ assetId, decoderConfig }) => ({ assetId, decoderConfig }))), [sources]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!isSupported || !canvas) return;
    const offscreen = canvas.transferControlToOffscreen();
    const worker = new Worker(new URL("./preview-compositor.worker.ts", import.meta.url)); workerRef.current = worker;
    worker.onmessage = (event: MessageEvent<{ type: string; message?: string; active?: boolean; assetIds?: string[]; requestId?: string; bitmap?: ImageBitmap }>) => {
      if (event.data.type === "error") setError(new Error(event.data.message ?? "Preview worker failed"));
      if (event.data.type === "buffering") { setBuffering(Boolean(event.data.active)); setBufferingAssetIds(event.data.assetIds ?? []); }
      if ((event.data.type === "ghost-frame" || event.data.type === "ghost-frame-missing") && event.data.requestId) {
        ghostRequestsRef.current.get(event.data.requestId)?.(event.data.bitmap ?? null);
        ghostRequestsRef.current.delete(event.data.requestId);
      }
    };
    worker.postMessage({ type: "init", canvas: offscreen, width: Math.round(previewMaxHeight * 16 / 9), height: previewMaxHeight, antiAliasing: antiAliasingEnabled, maxFrames: quality === "emergency" ? 12 : quality === "reduced" ? 30 : 60 }, [offscreen]);
    setReady(true);
    return () => { ghostRequestsRef.current.forEach((resolve) => resolve(null)); ghostRequestsRef.current.clear(); cancelAnimationFrame(animationRef.current ?? 0); worker.postMessage({ type: "dispose" }); worker.terminate(); workerRef.current = null; setReady(false); setBuffering(false); };
    // An OffscreenCanvas can only be transferred once for this mounted canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasRef, isSupported]);

  useEffect(() => { workerRef.current?.postMessage({ type: "quality", width: Math.round(previewMaxHeight * 16 / 9), height: previewMaxHeight, antiAliasing: antiAliasingEnabled, maxFrames: quality === "emergency" ? 12 : quality === "reduced" ? 30 : 60 }); }, [antiAliasingEnabled, previewMaxHeight, quality]);
  useEffect(() => { workerRef.current?.postMessage({ type: "timeline", clips, subtitles }); }, [clips, subtitles]);
  useEffect(() => {
    const push = (state: ReturnType<typeof useKeyframeLivePreviewStore.getState>, previous?: ReturnType<typeof useKeyframeLivePreviewStore.getState>) => {
      const transform = state.transform;
      workerRef.current?.postMessage({ type: "preview-transform", clipId: transform?.clipId ?? previous?.transform?.clipId ?? "", value: transform?.value ?? null });
    };
    push(useKeyframeLivePreviewStore.getState());
    return useKeyframeLivePreviewStore.subscribe((state, previous) => push(state, previous));
  }, []);
  useEffect(() => { for (const source of sources) workerRef.current?.postMessage({ type: "register-source", source }); }, [sourceKey, sources]);
  useEffect(() => {
    if (!lut) return;
    // Transfer a copy: the original LUT remains available to React state/UI.
    const data = lut.data.slice();
    workerRef.current?.postMessage({ type: "set-lut", lut: { dimension: lut.dimension, data: data.buffer } }, [data.buffer]);
  }, [lut]);

  const prefetchAround = useCallback((timeMs: number) => {
    if (!workerDemuxerModuleUrl || Math.abs(timeMs - lastPrefetch.current) < 250) return;
    lastPrefetch.current = timeMs;
    // A 4.5 s window makes quick back-and-forth scrubbing resolve from the LRU cache.
    for (const source of sources) {
      precacheKeyframeRange(source.proxyUrl, source.rangeIndex, timeMs);
      workerRef.current?.postMessage({ type: "load-proxy", assetId: source.assetId, demuxerModuleUrl: workerDemuxerModuleUrl, startUs: Math.max(0, timeMs - 1_500) * 1_000, endUs: (timeMs + 3_000) * 1_000 });
    }
  }, [sources, workerDemuxerModuleUrl]);
  useEffect(() => {
    let timer: number | undefined; let previous: { assetId: string; timeMs: number } | undefined;
    const unsubscribe = subscribeMediaHoverIntent((intent) => {
      if (!intent || !sources.some((source) => source.assetId === intent.assetId)) { if (timer) window.clearTimeout(timer); previous = undefined; return; }
      // Pointer jitter inside the same frame neighborhood should not keep postponing the 200ms intent threshold.
      if (previous?.assetId === intent.assetId && Math.abs(previous.timeMs - intent.timeMs) < 180) return;
      if (timer) window.clearTimeout(timer);
      previous = intent; timer = window.setTimeout(() => prefetchAround(intent.timeMs), 200);
    });
    return () => { if (timer) window.clearTimeout(timer); unsubscribe(); };
  }, [prefetchAround, sources]);
  const dispatchSeek = useCallback((timeMs: number, publishToReact: boolean) => {
    const value = Math.max(0, timeMs); timeRef.current = value;
    if (publishToReact || value - lastUiPublish.current >= 100) { lastUiPublish.current = value; setCurrentTimeMs(value); }
    workerRef.current?.postMessage({ type: "seek", timeMs: value }); prefetchAround(value); timeListenersRef.current.forEach((listener) => listener(value));
  }, [prefetchAround]);
  const seek = useCallback(async (timeMs: number) => { dispatchSeek(timeMs, true); }, [dispatchSeek]);
  const pause = useCallback(() => { playing.current = false; cancelAnimationFrame(animationRef.current ?? 0); lastTick.current = undefined; }, []);
  const play = useCallback((playbackRate = 1) => { playbackRateRef.current = playbackRate; if (playing.current) return; playing.current = true; const tick = (now: number) => { if (!playing.current) return; const elapsed = now - (lastTick.current ?? now); lastTick.current = now; dispatchSeek(Math.max(0, timeRef.current + elapsed * playbackRateRef.current), false); animationRef.current = requestAnimationFrame(tick); }; animationRef.current = requestAnimationFrame(tick); }, [dispatchSeek]);
  const subscribeTime = useCallback((listener: (timeMs: number) => void) => { timeListenersRef.current.add(listener); return () => timeListenersRef.current.delete(listener); }, []);
  const loadProxySegment = useCallback((assetId: string, startMs: number, endMs: number) => {
    if (!workerDemuxerModuleUrl) { setError(new Error("A worker-only MP4/WebM demuxer module is required for proxy playback")); return; }
    workerRef.current?.postMessage({ type: "load-proxy", assetId, demuxerModuleUrl: workerDemuxerModuleUrl, startUs: Math.max(0, startMs) * 1_000, endUs: Math.max(startMs, endMs) * 1_000 });
  }, [workerDemuxerModuleUrl]);
  const requestFrame = useCallback((assetId: string, timeMs: number) => new Promise<ImageBitmap | null>((resolve) => {
    const worker = workerRef.current; if (!worker) { resolve(null); return; }
    const requestId = `ghost-${assetId}-${Math.round(timeMs)}-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
    const timeout = window.setTimeout(() => { if (ghostRequestsRef.current.delete(requestId)) resolve(null); }, 450);
    ghostRequestsRef.current.set(requestId, (bitmap) => { window.clearTimeout(timeout); resolve(bitmap); });
    worker.postMessage({ type: "request-ghost-frame", requestId, assetId, timeMs });
  }), []);
  return { isSupported, isReady, isBuffering, bufferingAssetIds, currentTimeMs, error, seek, seekTo: seek, play, pause, subscribeTime, loadProxySegment, requestFrame };
}
