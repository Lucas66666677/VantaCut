"use client";

import { useEffect } from "react";

export interface MediaKeyframeRange { timeMs: number; startByte: number; endByte: number; }
export interface MediaRangeIndex { keyframes: MediaKeyframeRange[]; }
export interface MediaHoverIntent { assetId: string; timeMs: number; }
const HOVER_EVENT = "editor-media-hover-intent";

/** Registers the Service Worker once per editor client. It persists only HTTP 206 ranges. */
export function useMediaRangeCacheServiceWorker() {
  useEffect(() => { if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/media-range-cache-sw.js", { scope: "/" }).catch(() => undefined); }, []);
}

export function precacheKeyframeRange(url: string, index: MediaRangeIndex | undefined, timeMs: number) {
  if (!index?.keyframes.length || !("serviceWorker" in navigator)) return;
  const frame = index.keyframes.reduce<MediaKeyframeRange | undefined>((winner, candidate) => candidate.timeMs <= timeMs && (!winner || candidate.timeMs > winner.timeMs) ? candidate : winner, undefined) ?? index.keyframes[0];
  if (!frame) return;
  const range = `bytes=${Math.max(0, frame.startByte)}-${Math.max(frame.startByte, frame.endByte)}`;
  const post = () => navigator.serviceWorker.controller?.postMessage({ type: "precache-media-range", url, range });
  if (navigator.serviceWorker.controller) post(); else void navigator.serviceWorker.ready.then(post).catch(() => undefined);
}

export function dispatchMediaHoverIntent(intent: MediaHoverIntent | null) { window.dispatchEvent(new CustomEvent<MediaHoverIntent | null>(HOVER_EVENT, { detail: intent })); }
export function subscribeMediaHoverIntent(listener: (intent: MediaHoverIntent | null) => void) {
  const handler = (event: Event) => listener((event as CustomEvent<MediaHoverIntent | null>).detail);
  window.addEventListener(HOVER_EVENT, handler); return () => window.removeEventListener(HOVER_EVENT, handler);
}
