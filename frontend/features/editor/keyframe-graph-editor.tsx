"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import type { ClipTransformAnimation, CubicBezier } from "@/types/keyframes";

type Property = "x" | "y" | "scale";
const PROPERTIES: Array<[Property, string, string]> = [["x", "Position X", "#38bdf8"], ["y", "Position Y", "#f472b6"], ["scale", "Scale", "#a78bfa"]];
const WIDTH = 720; const HEIGHT = 230; const PAD = 30;

function curveFor(frame: ClipTransformAnimation["keyframes"][number]): CubicBezier { return frame.cubic_bezier ?? { x1: .42, y1: 0, x2: .58, y2: 1 }; }

interface Props { animation: ClipTransformAnimation; duration: number; onChange: (animation: ClipTransformAnimation) => void; }

/** Canvas curve raster + SVG hit targets: crisp at any DPR, while nodes stay vector-sharp and directly manipulable. */
export function KeyframeGraphEditor({ animation, duration, onChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null); const [property, setProperty] = useState<Property>("x"); const [selected, setSelected] = useState(0);
  const frames = useMemo(() => [...animation.keyframes].sort((left, right) => left.time - right.time), [animation]);
  const values = frames.map((frame) => frame.value[property]); const min = Math.min(...values, property === "scale" ? .8 : 0); const max = Math.max(...values, property === "scale" ? 1.2 : 1); const span = Math.max(.01, max - min);
  const xFor = (time: number) => PAD + Math.max(0, Math.min(1, time / Math.max(.001, duration))) * (WIDTH - PAD * 2);
  const yFor = (value: number) => HEIGHT - PAD - (value - min) / span * (HEIGHT - PAD * 2);
  const valueForY = (y: number) => min + (HEIGHT - PAD - y) / (HEIGHT - PAD * 2) * span;
  const path = useMemo(() => frames.slice(0, -1).map((frame, index) => {
    const next = frames[index + 1]; const curve = curveFor(frame); const delta = next.time - frame.time; const valueDelta = next.value[property] - frame.value[property];
    return `${index ? "" : `M ${xFor(frame.time)} ${yFor(frame.value[property])}`} C ${xFor(frame.time + delta * curve.x1)} ${yFor(frame.value[property] + valueDelta * curve.y1)}, ${xFor(frame.time + delta * curve.x2)} ${yFor(frame.value[property] + valueDelta * curve.y2)}, ${xFor(next.time)} ${yFor(next.value[property])}`;
  }).join(" "), [frames, property, duration, min, max]);
  const color = PROPERTIES.find(([key]) => key === property)![2];

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return; const dpr = window.devicePixelRatio || 1; canvas.width = WIDTH * dpr; canvas.height = HEIGHT * dpr; canvas.style.width = "100%"; canvas.style.height = "100%";
    const context = canvas.getContext("2d"); if (!context) return; context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, WIDTH, HEIGHT);
    context.strokeStyle = "rgba(161,161,170,.18)"; context.lineWidth = 1; for (let index = 0; index < 5; index += 1) { const y = PAD + index * (HEIGHT - PAD * 2) / 4; context.beginPath(); context.moveTo(PAD, y); context.lineTo(WIDTH - PAD, y); context.stroke(); }
    context.strokeStyle = color; context.lineWidth = 2.25; context.lineJoin = "round"; context.lineCap = "round"; const graph = new Path2D(path); context.stroke(graph);
  }, [color, path]);

  const updateCurve = (segmentIndex: number, patch: Partial<CubicBezier>) => {
    const next = structuredClone(animation); const frame = next.keyframes[segmentIndex]; frame.easing = "cubic-bezier"; frame.cubic_bezier = { ...curveFor(frame), ...patch }; onChange(next);
  };
  const updateHandle = (kind: "in" | "out", event: ReactPointerEvent<SVGCircleElement>) => {
    const index = kind === "out" ? selected : selected - 1; if (index < 0 || index >= frames.length - 1) return;
    const svg = event.currentTarget.ownerSVGElement; if (!svg) return; event.currentTarget.setPointerCapture(event.pointerId);
    const move = (moveEvent: PointerEvent) => {
      const rect = svg.getBoundingClientRect(); const x = (moveEvent.clientX - rect.left) / rect.width * WIDTH; const y = (moveEvent.clientY - rect.top) / rect.height * HEIGHT;
      const from = frames[index]; const to = frames[index + 1]; const graphTime = Math.max(0, Math.min(duration, (x - PAD) / (WIDTH - PAD * 2) * duration)); const timeProgress = Math.max(0, Math.min(1, (graphTime - from.time) / Math.max(.001, to.time - from.time)));
      const valueProgress = Math.max(-2, Math.min(2, (valueForY(y) - from.value[property]) / Math.max(.0001, to.value[property] - from.value[property])));
      updateCurve(index, kind === "out" ? { x1: timeProgress, y1: valueProgress } : { x2: timeProgress, y2: valueProgress });
    };
    const end = () => { event.currentTarget.removeEventListener("pointermove", move); event.currentTarget.removeEventListener("pointerup", end); event.currentTarget.removeEventListener("pointercancel", end); };
    event.currentTarget.addEventListener("pointermove", move); event.currentTarget.addEventListener("pointerup", end); event.currentTarget.addEventListener("pointercancel", end);
  };
  const incoming = selected > 0 ? { frame: frames[selected - 1], from: frames[selected - 1], to: frames[selected] } : null;
  const outgoing = selected < frames.length - 1 ? { frame: frames[selected], from: frames[selected], to: frames[selected + 1] } : null;
  const handlePoint = (entry: NonNullable<typeof incoming>, kind: "in" | "out") => { const curve = curveFor(entry.frame); const control = kind === "out" ? { x: curve.x1, y: curve.y1 } : { x: curve.x2, y: curve.y2 }; const dt = entry.to.time - entry.from.time; const dv = entry.to.value[property] - entry.from.value[property]; return { x: xFor(entry.from.time + dt * control.x), y: yFor(entry.from.value[property] + dv * control.y) }; };
  const currentPoint = frames[selected] ? { x: xFor(frames[selected].time), y: yFor(frames[selected].value[property]) } : null;

  return <div className="rounded-xl border border-violet-400/35 bg-zinc-950 p-2"><div className="mb-2 flex gap-1">{PROPERTIES.map(([key, label, shade]) => <button key={key} type="button" onClick={() => setProperty(key)} className={`rounded px-2 py-1 text-[10px] ${property === key ? "bg-violet-400/20 text-violet-100" : "text-zinc-500 hover:text-zinc-200"}`} style={property === key ? { boxShadow: `inset 0 -1px 0 ${shade}` } : undefined}>{label}</button>)}</div>
    <div className="relative aspect-[720/230]"><canvas ref={canvasRef} className="absolute inset-0 h-full w-full" /><svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="absolute inset-0 h-full w-full overflow-visible" aria-label={`${property} keyframe graph`}><path d={path} fill="none" stroke="transparent" strokeWidth="12" />{frames.map((frame, index) => <circle key={`${frame.time}-${index}`} cx={xFor(frame.time)} cy={yFor(frame.value[property])} r={selected === index ? 6 : 4} fill={selected === index ? "#f5f3ff" : color} stroke="#18181b" strokeWidth="2" className="cursor-pointer" onPointerDown={() => setSelected(index)} />)}{currentPoint && incoming && (() => { const control = handlePoint(incoming, "in"); return <g><line x1={currentPoint.x} y1={currentPoint.y} x2={control.x} y2={control.y} stroke="#a78bfa" strokeDasharray="3 3" /><circle cx={control.x} cy={control.y} r="4" fill="#c4b5fd" className="cursor-grab" onPointerDown={(event) => updateHandle("in", event)} /></g>; })()}{currentPoint && outgoing && (() => { const control = handlePoint(outgoing, "out"); return <g><line x1={currentPoint.x} y1={currentPoint.y} x2={control.x} y2={control.y} stroke="#a78bfa" strokeDasharray="3 3" /><circle cx={control.x} cy={control.y} r="4" fill="#c4b5fd" className="cursor-grab" onPointerDown={(event) => updateHandle("out", event)} /></g>; })()}</svg></div>
    <div className="mt-1 flex justify-between px-1 text-[9px] text-zinc-500"><span>{min.toFixed(2)}</span><span>選取關鍵幀後拖曳入／出切線手柄</span><span>{max.toFixed(2)}</span></div>
  </div>;
}
