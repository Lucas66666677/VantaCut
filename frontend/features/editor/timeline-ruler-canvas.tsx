"use client";

import { useEffect, useRef, type RefObject } from "react";

function stepForZoom(zoom: number): number {
  if (zoom >= 220) return .1;
  if (zoom >= 140) return .25;
  if (zoom >= 80) return .5;
  if (zoom >= 42) return 1;
  return 2;
}

/** Canvas ruler: one drawing surface instead of hundreds or thousands of tick DOM nodes. */
export function TimelineRulerCanvas({ scrollerRef, zoom, duration }: { scrollerRef: RefObject<HTMLDivElement | null>; zoom: number; duration: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null); const frameRef = useRef<number | null>(null);
  useEffect(() => {
    const scroller = scrollerRef.current; const canvas = canvasRef.current; if (!scroller || !canvas) return;
    const draw = () => {
      frameRef.current = null;
      const width = scroller.clientWidth; const height = 36; const ratio = window.devicePixelRatio || 1;
      canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
      if (canvas.width !== width * ratio || canvas.height !== height * ratio) { canvas.width = width * ratio; canvas.height = height * ratio; }
      const context = canvas.getContext("2d"); if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, width, height);
      const start = Math.max(0, scroller.scrollLeft / zoom); const end = Math.min(duration, (scroller.scrollLeft + width) / zoom);
      const step = stepForZoom(zoom); const first = Math.floor(start / step) * step;
      context.font = "10px ui-sans-serif, system-ui, sans-serif"; context.textBaseline = "middle";
      for (let time = first; time <= end + step; time += step) {
        const x = Math.round(time * zoom - scroller.scrollLeft) + .5;
        const whole = Math.abs(time - Math.round(time)) < .001;
        context.strokeStyle = whole ? "rgba(113,113,122,.65)" : "rgba(63,63,70,.65)";
        context.beginPath(); context.moveTo(x, whole ? 0 : 15); context.lineTo(x, height); context.stroke();
        if (whole) { context.fillStyle = "#71717a"; context.fillText(`${Math.round(time)}s`, x + 4, 10); }
      }
    };
    const schedule = () => { if (frameRef.current === null) frameRef.current = requestAnimationFrame(draw); };
    const observer = new ResizeObserver(schedule); observer.observe(scroller); scroller.addEventListener("scroll", schedule, { passive: true }); schedule();
    return () => { scroller.removeEventListener("scroll", schedule); observer.disconnect(); if (frameRef.current !== null) cancelAnimationFrame(frameRef.current); };
  }, [duration, scrollerRef, zoom]);
  return <canvas ref={canvasRef} aria-label="時間尺規" className="sticky left-0 z-20 block h-9 bg-zinc-950/95 backdrop-blur" />;
}
