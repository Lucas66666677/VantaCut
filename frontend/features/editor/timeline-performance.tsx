"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject, type WheelEvent } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import type { ClipLayout, TrackType } from "@/types/timeline";
import { animateSnapSpring } from "@/features/editor/semantic-snapping";
import { create } from "zustand";

const OVERSCAN_SECONDS = 3;
// 24 active + at most 24 Framer Motion exit nodes keeps Clip roots below 50.
const MAX_RENDERED_CLIPS = 24;

function lowerBoundByEnd(clips: ClipLayout[], time: number): number {
  let low = 0; let high = clips.length;
  while (low < high) { const middle = (low + high) >>> 1; if (clips[middle].displayEnd < time) low = middle + 1; else high = middle; }
  return low;
}
function upperBoundByStart(clips: ClipLayout[], time: number): number {
  let low = 0; let high = clips.length;
  while (low < high) { const middle = (low + high) >>> 1; if (clips[middle].displayStart <= time) low = middle + 1; else high = middle; }
  return low;
}

/**
 * Bidirectional virtual window with a strict node budget. Candidate lookup is O(log n)
 * per lane, then the closest visible clips win; offscreen nodes are unmounted.
 */
export function useTimelineVirtualWindow(layouts: Map<TrackType, ClipLayout[]>, zoom: number, scrollerRef: RefObject<HTMLDivElement | null>, pinnedClipId?: string | null) {
  const [viewport, setViewport] = useState({ left: 0, width: 1 });
  const frameRef = useRef<number | null>(null);
  const measure = useCallback(() => {
    const element = scrollerRef.current; if (!element) return;
    setViewport({ left: element.scrollLeft, width: element.clientWidth });
  }, [scrollerRef]);

  useEffect(() => {
    const element = scrollerRef.current; if (!element) return;
    const onScroll = () => {
      if (frameRef.current !== null) return;
      frameRef.current = requestAnimationFrame(() => { frameRef.current = null; measure(); });
    };
    const observer = new ResizeObserver(measure);
    element.addEventListener("scroll", onScroll, { passive: true }); observer.observe(element); measure();
    return () => { element.removeEventListener("scroll", onScroll); observer.disconnect(); if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); };
  }, [measure, scrollerRef]);

  const windowStart = Math.max(0, viewport.left / zoom - OVERSCAN_SECONDS);
  const windowEnd = (viewport.left + viewport.width) / zoom + OVERSCAN_SECONDS;
  return useMemo(() => {
    const candidates: Array<{ track: TrackType; clip: ClipLayout; inViewport: boolean; distance: number }> = [];
    const center = (windowStart + windowEnd) / 2;
    for (const [track, clips] of layouts) {
      const start = lowerBoundByEnd(clips, windowStart);
      const end = upperBoundByStart(clips, windowEnd);
      for (const clip of clips.slice(start, end)) {
        const inViewport = clip.displayEnd >= viewport.left / zoom && clip.displayStart <= (viewport.left + viewport.width) / zoom;
        candidates.push({ track, clip, inViewport, distance: Math.abs((clip.displayStart + clip.displayEnd) / 2 - center) });
      }
    }
    candidates.sort((left, right) => Number(right.inViewport) - Number(left.inViewport) || left.distance - right.distance);
    const selected = new Set(candidates.slice(0, MAX_RENDERED_CLIPS).map((candidate) => candidate.clip.id));
    const result = new Map<TrackType, ClipLayout[]>();
    for (const track of layouts.keys()) result.set(track, candidates.filter((candidate) => candidate.track === track && selected.has(candidate.clip.id)).map((candidate) => candidate.clip).sort((left, right) => left.displayStart - right.displayStart));
    // A dragged or selected Clip must not disappear when it crosses the overscan boundary.
    if (pinnedClipId && !selected.has(pinnedClipId)) {
      for (const [track, clips] of layouts) {
        const pinned = clips.find((clip) => clip.id === pinnedClipId);
        if (!pinned) continue;
        result.set(track, [...(result.get(track) ?? []), pinned].sort((left, right) => left.displayStart - right.displayStart));
        break;
      }
    }
    return result;
  }, [layouts, pinnedClipId, viewport.left, viewport.width, windowEnd, windowStart, zoom]);
}

/**
 * A playhead intentionally does not subscribe through React. Zustand notifies this
 * DOM writer directly, so 60 Hz playback cannot re-render hundreds of Clip nodes.
 */
export function TransientPlayhead({ zoom }: { zoom: number }) {
  const elementRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  useEffect(() => {
    zoomRef.current = zoom;
    const time = useTimelineStore.getState().playheadTime;
    elementRef.current?.style.setProperty("transform", `translate3d(${time * zoom}px, 0, 0)`);
  }, [zoom]);
  useEffect(() => useTimelineStore.subscribe((state, previous) => {
    if (state.playheadTime === previous.playheadTime) return;
    elementRef.current?.style.setProperty("transform", `translate3d(${state.playheadTime * zoomRef.current}px, 0, 0)`);
  }), []);
  return <div ref={elementRef} className="pointer-events-none absolute bottom-0 top-0 z-40 w-px will-change-transform" aria-hidden="true"><div className="absolute -left-1.5 -top-1 h-3 w-3 rotate-45 bg-amber-400" /></div>;
}

interface TransientSnapGuide { time: number; label: string; active: boolean; }
const useTimelineTransientStore = create<{ snapGuide: TransientSnapGuide; setSnapGuide: (guide: TransientSnapGuide) => void }>((set) => ({
  snapGuide: { time: 0, label: "", active: false },
  setSnapGuide: (snapGuide) => set({ snapGuide }),
}));

export function setTimelineSnapGuide(guide: TransientSnapGuide): void {
  useTimelineTransientStore.getState().setSnapGuide(guide);
}

/** Like the playhead, the snap guide is a DOM subscriber and never re-renders the Timeline. */
export function TransientSnapGuide({ zoom }: { zoom: number }) {
  const lineRef = useRef<HTMLDivElement>(null); const labelRef = useRef<HTMLDivElement>(null); const zoomRef = useRef(zoom);
  const apply = useCallback((guide: TransientSnapGuide) => {
    const line = lineRef.current; if (!line) return;
    line.style.transform = `translate3d(${guide.time * zoomRef.current}px, 0, 0)`;
    line.style.opacity = guide.active ? "1" : "0";
    line.style.visibility = guide.active ? "visible" : "hidden";
    if (labelRef.current) labelRef.current.textContent = guide.label;
  }, []);
  useEffect(() => { zoomRef.current = zoom; apply(useTimelineTransientStore.getState().snapGuide); }, [apply, zoom]);
  useEffect(() => useTimelineTransientStore.subscribe((state, previous) => { if (state.snapGuide !== previous.snapGuide) apply(state.snapGuide); }), [apply]);
  return <div ref={lineRef} className="pointer-events-none absolute bottom-0 top-0 z-30 w-px bg-cyan-100 opacity-0 shadow-[0_0_10px_rgba(103,232,249,.95)] will-change-transform"><div ref={labelRef} className="absolute left-1 top-1 rounded bg-cyan-400 px-1.5 py-0.5 text-[10px] font-medium text-zinc-950 shadow" /></div>;
}

interface ZeroRenderScrubOptions {
  surfaceRef: RefObject<HTMLDivElement | null>;
  scrollerRef: RefObject<HTMLDivElement | null>;
  zoom: number;
  duration: number;
  resolve: (time: number) => { time: number; point: { time_seconds: number; label: string } | null };
  onStart?: (time: number) => void;
  onScrub?: (time: number, velocityPxPerMs: number) => void;
  onEnd?: () => void;
}

/** Native PointerEvent listeners drive the Zustand store directly; no React state changes occur while scrubbing. */
export function useZeroRenderScrubbing({ surfaceRef, scrollerRef, zoom, duration, resolve, onStart, onScrub, onEnd }: ZeroRenderScrubOptions) {
  const configRef = useRef({ zoom, duration, resolve, onStart, onScrub });
  const snapTargetRef = useRef<number | null>(null);
  const pointerRef = useRef<{ x: number; at: number } | null>(null);
  useEffect(() => { configRef.current = { zoom, duration, resolve, onStart, onScrub }; }, [duration, onScrub, onStart, resolve, zoom]);
  useEffect(() => {
    const surface = surfaceRef.current; if (!surface) return;
    const update = (event: PointerEvent, emitAudio = true) => {
      const config = configRef.current; const rect = surface.getBoundingClientRect();
      const now = performance.now(); const previous = pointerRef.current; const velocity = previous ? (event.clientX - previous.x) / Math.max(1, now - previous.at) : 0;
      pointerRef.current = { x: event.clientX, at: now };
      const raw = Math.min(config.duration, Math.max(0, (event.clientX - rect.left + (scrollerRef.current?.scrollLeft ?? 0)) / config.zoom));
      const resolved = config.resolve(raw);
      useTimelineStore.getState().setPlayheadTime(resolved.time);
      snapTargetRef.current = resolved.point?.time_seconds ?? null;
      setTimelineSnapGuide(resolved.point ? { time: resolved.point.time_seconds, label: resolved.point.label, active: true } : { time: resolved.time, label: "", active: false });
      if (emitAudio) config.onScrub?.(resolved.time, velocity);
      return resolved.time;
    };
    const down = (event: PointerEvent) => {
      if (surface.closest("[data-space-panning='true']")) return;
      if ((event.target as Element | null)?.closest("[data-timeline-clip], [data-intent-menu], button, input")) return;
      surface.setPointerCapture(event.pointerId); pointerRef.current = { x: event.clientX, at: performance.now() }; const time = update(event, false); configRef.current.onStart?.(time);
    };
    const move = (event: PointerEvent) => { if (surface.hasPointerCapture(event.pointerId)) update(event); };
    const release = (event: PointerEvent) => {
      if (!surface.hasPointerCapture(event.pointerId)) return;
      surface.releasePointerCapture(event.pointerId);
      const target = snapTargetRef.current;
      if (target !== null) animateSnapSpring(useTimelineStore.getState().playheadTime, target, useTimelineStore.getState().setPlayheadTime);
      snapTargetRef.current = null; setTimelineSnapGuide({ time: 0, label: "", active: false }); onEnd?.();
      pointerRef.current = null;
    };
    surface.addEventListener("pointerdown", down); surface.addEventListener("pointermove", move); surface.addEventListener("pointerup", release); surface.addEventListener("pointercancel", release);
    return () => { surface.removeEventListener("pointerdown", down); surface.removeEventListener("pointermove", move); surface.removeEventListener("pointerup", release); surface.removeEventListener("pointercancel", release); };
  }, [onEnd, scrollerRef, surfaceRef]);
}

interface SmoothZoomOptions {
  zoom: number;
  setZoom: (zoom: number) => void;
  scrollerRef: RefObject<HTMLDivElement | null>;
  surfaceRef: RefObject<HTMLDivElement | null>;
}

/** CSS pre-scale immediately, then commit zoom + virtualization after wheel activity settles. */
export function useSmoothTimelineZoom({ zoom, setZoom, scrollerRef, surfaceRef }: SmoothZoomOptions) {
  const committedZoomRef = useRef(zoom);
  const visualZoomRef = useRef(zoom);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const anchorRef = useRef<{ time: number; pointerX: number } | null>(null);
  useEffect(() => { committedZoomRef.current = zoom; visualZoomRef.current = zoom; }, [zoom]);
  useEffect(() => () => { if (idleTimerRef.current) clearTimeout(idleTimerRef.current); }, []);

  return useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const scroller = scrollerRef.current; const surface = surfaceRef.current; if (!scroller || !surface) return;
    const bounds = scroller.getBoundingClientRect(); const pointerX = event.clientX - bounds.left;
    const currentZoom = visualZoomRef.current;
    const anchorTime = (scroller.scrollLeft + pointerX) / currentZoom;
    const nextZoom = Math.max(24, Math.min(240, currentZoom * Math.exp(-event.deltaY * 0.0022)));
    visualZoomRef.current = nextZoom; anchorRef.current = { time: anchorTime, pointerX };
    const originX = scroller.scrollLeft + pointerX;
    surface.style.setProperty("--timeline-zoom-origin", `${originX}px 0`);
    surface.style.setProperty("--timeline-zoom-scale", `${nextZoom / committedZoomRef.current}`);
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      const anchor = anchorRef.current; if (!anchor) return;
      setZoom(nextZoom);
      requestAnimationFrame(() => {
        // Keep the exact time beneath the cursor when layout width catches up.
        scroller.scrollLeft = Math.max(0, anchor.time * nextZoom - anchor.pointerX);
        surface.style.setProperty("--timeline-zoom-scale", "1"); surface.style.setProperty("--timeline-zoom-origin", "0 0");
      });
    }, 110);
  }, [scrollerRef, setZoom, surfaceRef]);
}

/** Smooth edge scrolling for pointer drags; velocity rises quadratically near an edge. */
export function useTimelineAutoScroll(scrollerRef: RefObject<HTMLDivElement | null>) {
  const velocityRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const stop = useCallback(() => {
    velocityRef.current = 0;
    if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null; }
  }, []);
  const tick = useCallback(() => {
    const scroller = scrollerRef.current;
    if (!scroller || velocityRef.current === 0) { frameRef.current = null; return; }
    scroller.scrollLeft += velocityRef.current;
    frameRef.current = requestAnimationFrame(tick);
  }, [scrollerRef]);
  const update = useCallback((clientX: number) => {
    const scroller = scrollerRef.current; if (!scroller) return;
    const rect = scroller.getBoundingClientRect(); const edge = Math.min(96, rect.width * .18);
    const fromLeft = clientX - rect.left; const fromRight = rect.right - clientX;
    let velocity = 0;
    if (fromLeft >= 0 && fromLeft < edge) velocity = -Math.pow(1 - fromLeft / edge, 2) * 28;
    if (fromRight >= 0 && fromRight < edge) velocity = Math.pow(1 - fromRight / edge, 2) * 28;
    velocityRef.current = velocity;
    if (velocity !== 0 && frameRef.current === null) frameRef.current = requestAnimationFrame(tick);
    if (velocity === 0) stop();
  }, [scrollerRef, stop, tick]);
  useEffect(() => stop, [stop]);
  return { update, stop };
}
