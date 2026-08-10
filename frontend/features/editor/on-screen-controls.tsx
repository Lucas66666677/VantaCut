"use client";

import { useEffect, useRef } from "react";

import { applyMat3, OSC_VIEW_HEIGHT, OSC_VIEW_WIDTH, screenToVideoPoint, snapTransform, transformMatrix, type OscGuides, type OscTransform, videoToLocal } from "@/features/editor/osc-geometry";

type Handle = "move" | "rotate" | "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
interface OnScreenControlsProps { target: OscTransform; onCommit: (transform: OscTransform) => void; onPreview?: (transform: OscTransform) => void; }

const HANDLE_POSITIONS: Array<[Exclude<Handle, "move" | "rotate">, number, number]> = [["nw", -1, -1], ["n", 0, -1], ["ne", 1, -1], ["e", 1, 0], ["se", 1, 1], ["s", 0, 1], ["sw", -1, 1], ["w", -1, 0]];
const clampScale = (value: number) => Math.min(4, Math.max(.15, value));

/** SVG OSC overlay: every move mutates a <g> transform directly; React is only notified on commit. */
export function OnScreenControls({ target, onCommit, onPreview }: OnScreenControlsProps) {
  const svgRef = useRef<SVGSVGElement>(null); const boxRef = useRef<SVGGElement>(null); const verticalGuide = useRef<SVGLineElement>(null); const horizontalGuide = useRef<SVGLineElement>(null); const angleGuide = useRef<SVGCircleElement>(null);
  const live = useRef(target); const active = useRef<{ handle: Handle; start: OscTransform; startLocal: { x: number; y: number } } | null>(null);
  useEffect(() => { live.current = target; draw(target, { horizontal: false, vertical: false, angle: false }); }, [target]);

  const draw = (next: OscTransform, guides: OscGuides) => {
    const width = next.width * OSC_VIEW_WIDTH; const height = next.height * OSC_VIEW_HEIGHT;
    // translate · rotate · scale is composed as an affine 3×3 matrix before mapping to SVG.
    const center = applyMat3(transformMatrix(next), { x: 0, y: 0 });
    boxRef.current?.setAttribute("transform", `translate(${center.x} ${center.y}) rotate(${next.rotation}) scale(${next.scale})`);
    boxRef.current?.setAttribute("data-width", String(width));
    verticalGuide.current?.setAttribute("opacity", guides.vertical ? "1" : "0"); horizontalGuide.current?.setAttribute("opacity", guides.horizontal ? "1" : "0"); angleGuide.current?.setAttribute("opacity", guides.angle ? "1" : "0");
  };
  const setLive = (next: OscTransform, guides: OscGuides) => { live.current = next; draw(next, guides); onPreview?.(next); };

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const current = active.current; const svg = svgRef.current; if (!current || !svg) return;
      const point = screenToVideoPoint(event.clientX, event.clientY, svg.getBoundingClientRect());
      let next: OscTransform;
      if (current.handle === "move") next = { ...current.start, x: point.x, y: point.y };
      else if (current.handle === "rotate") {
        const radians = Math.atan2((point.y - current.start.y) * OSC_VIEW_HEIGHT, (point.x - current.start.x) * OSC_VIEW_WIDTH);
        next = { ...current.start, rotation: radians * 180 / Math.PI + 90 };
      } else {
        const local = videoToLocal(point, current.start); const initialDistance = Math.max(1, Math.hypot(current.startLocal.x, current.startLocal.y));
        const nextDistance = Math.hypot(local.x, local.y);
        next = { ...current.start, scale: clampScale(current.start.scale * nextDistance / initialDistance) };
      }
      const snapped = snapTransform(next); setLive(snapped.transform, snapped.guides);
    };
    const release = () => { if (!active.current) return; const value = live.current; active.current = null; onCommit(value); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", release); window.addEventListener("pointercancel", release);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", release); window.removeEventListener("pointercancel", release); };
  }, [onCommit, onPreview]);

  const begin = (event: React.PointerEvent<SVGElement>, handle: Handle) => {
    event.preventDefault(); event.stopPropagation();
    const svg = svgRef.current; if (!svg) return;
    const start = live.current; const point = screenToVideoPoint(event.clientX, event.clientY, svg.getBoundingClientRect());
    active.current = { handle, start, startLocal: videoToLocal(point, start) };
  };
  const width = target.width * OSC_VIEW_WIDTH; const height = target.height * OSC_VIEW_HEIGHT; const halfWidth = width / 2; const halfHeight = height / 2;
  const initialCenter = applyMat3(transformMatrix(target), { x: 0, y: 0 });
  return <svg ref={svgRef} viewBox={`0 0 ${OSC_VIEW_WIDTH} ${OSC_VIEW_HEIGHT}`} preserveAspectRatio="none" className="pointer-events-none absolute inset-0 z-40 h-full w-full overflow-visible" aria-label="元素操控控制盒">
    <line ref={verticalGuide} x1={OSC_VIEW_WIDTH / 2} x2={OSC_VIEW_WIDTH / 2} y1="0" y2={OSC_VIEW_HEIGHT} stroke="#67e8f9" strokeWidth="1.5" strokeDasharray="5 5" opacity="0" className="drop-shadow-[0_0_3px_rgba(103,232,249,.9)]" />
    <line ref={horizontalGuide} x1="0" x2={OSC_VIEW_WIDTH} y1={OSC_VIEW_HEIGHT / 2} y2={OSC_VIEW_HEIGHT / 2} stroke="#67e8f9" strokeWidth="1.5" strokeDasharray="5 5" opacity="0" className="drop-shadow-[0_0_3px_rgba(103,232,249,.9)]" />
    <circle ref={angleGuide} cx={OSC_VIEW_WIDTH / 2} cy={OSC_VIEW_HEIGHT / 2} r="18" fill="none" stroke="#67e8f9" strokeWidth="2" opacity="0" />
    <g ref={boxRef} transform={`translate(${initialCenter.x} ${initialCenter.y}) rotate(${target.rotation}) scale(${target.scale})`}>
      <rect x={-halfWidth} y={-halfHeight} width={width} height={height} fill="transparent" stroke="#a5f3fc" strokeWidth="2" vectorEffect="non-scaling-stroke" className="pointer-events-auto cursor-move" onPointerDown={(event) => begin(event, "move")} />
      <line x1="0" x2="0" y1={-halfHeight} y2={-halfHeight - 36} stroke="#a5f3fc" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <circle cx="0" cy={-halfHeight - 42} r="8" fill="#082f49" stroke="#a5f3fc" strokeWidth="2" vectorEffect="non-scaling-stroke" className="pointer-events-auto cursor-grab" onPointerDown={(event) => begin(event, "rotate")} />
      {HANDLE_POSITIONS.map(([handle, x, y]) => <rect key={handle} x={x * halfWidth - 5} y={y * halfHeight - 5} width="10" height="10" rx="2" fill="#ecfeff" stroke="#0891b2" strokeWidth="1.5" vectorEffect="non-scaling-stroke" className="pointer-events-auto" onPointerDown={(event) => begin(event, handle)} />)}
    </g>
  </svg>;
}
