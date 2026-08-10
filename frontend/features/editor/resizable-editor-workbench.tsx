"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { AspectRatioDampenedFrame } from "@/features/editor/aspect-ratio-dampened-frame";

/** CSS Grid split view. The divider mutates a CSS variable; ResizeObserver publishes settled sizes only. */
export function ResizableEditorWorkbench({ preview, timeline, className = "" }: { preview: ReactNode; timeline: ReactNode; className?: string }) {
  const gridRef = useRef<HTMLDivElement>(null); const [previewHeight, setPreviewHeight] = useState(420); const dragRef = useRef<number | null>(null); const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const grid = gridRef.current; if (!grid) return;
    const observer = new ResizeObserver(() => { if (debounceRef.current) clearTimeout(debounceRef.current); debounceRef.current = setTimeout(() => setPreviewHeight(Number.parseFloat(getComputedStyle(grid).getPropertyValue("--preview-height")) || 420), 80); });
    observer.observe(grid); return () => { observer.disconnect(); if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);
  return <section ref={gridRef} className={`grid min-h-[620px] overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 [grid-template-rows:var(--preview-height)_8px_minmax(180px,1fr)] ${className}`} style={{ "--preview-height": `${previewHeight}px` } as React.CSSProperties}>
    <div className="min-h-0 p-3"><AspectRatioDampenedFrame className="rounded-xl bg-black">{preview}</AspectRatioDampenedFrame></div>
    <div role="separator" aria-orientation="horizontal" aria-label="調整預覽與時間軸比例" onPointerDown={(event) => { dragRef.current = event.pointerId; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (dragRef.current !== event.pointerId || !gridRef.current) return; const bounds = gridRef.current.getBoundingClientRect(); const height = Math.max(180, Math.min(bounds.height - 180, event.clientY - bounds.top)); gridRef.current.style.setProperty("--preview-height", `${height}px`); }} onPointerUp={(event) => { if (dragRef.current !== event.pointerId || !gridRef.current) return; dragRef.current = null; event.currentTarget.releasePointerCapture(event.pointerId); setPreviewHeight(Number.parseFloat(getComputedStyle(gridRef.current).getPropertyValue("--preview-height"))); }} className="group cursor-row-resize bg-zinc-900 will-change-[height] hover:bg-cyan-400/30"><div className="mx-auto mt-3 h-0.5 w-10 rounded-full bg-zinc-600 transition group-hover:bg-cyan-200" /></div>
    <div className="min-h-0 overflow-auto p-3 will-change-[width,height]">{timeline}</div>
  </section>;
}
