"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AnnotationOperation, FrameAnnotation } from "@/types/review";

type Tool = "pen" | "circle" | "text";

interface ReviewAnnotationCanvasProps {
  annotation: FrameAnnotation;
  editable?: boolean;
  onChange?: (annotation: FrameAnnotation) => void;
}

const CANVAS_WIDTH = 1280;
const CANVAS_HEIGHT = 720;
const RED = "#ff334f";

function drawOperation(context: CanvasRenderingContext2D, operation: AnnotationOperation) {
  context.save();
  context.strokeStyle = operation.color;
  context.fillStyle = operation.color;
  context.lineCap = "round";
  context.lineJoin = "round";
  if (operation.kind === "stroke") {
    if (operation.points.length < 2) return;
    context.lineWidth = operation.width;
    context.beginPath();
    context.moveTo(operation.points[0].x * CANVAS_WIDTH, operation.points[0].y * CANVAS_HEIGHT);
    operation.points.slice(1).forEach((point) => context.lineTo(point.x * CANVAS_WIDTH, point.y * CANVAS_HEIGHT));
    context.stroke();
  } else if (operation.kind === "circle") {
    context.lineWidth = operation.width;
    const left = Math.min(operation.start.x, operation.end.x) * CANVAS_WIDTH;
    const top = Math.min(operation.start.y, operation.end.y) * CANVAS_HEIGHT;
    const width = Math.abs(operation.end.x - operation.start.x) * CANVAS_WIDTH;
    const height = Math.abs(operation.end.y - operation.start.y) * CANVAS_HEIGHT;
    context.beginPath();
    context.ellipse(left + width / 2, top + height / 2, Math.max(1, width / 2), Math.max(1, height / 2), 0, 0, Math.PI * 2);
    context.stroke();
  } else {
    context.font = `${operation.fontSize}px sans-serif`;
    context.fillText(operation.text, operation.x * CANVAS_WIDTH, operation.y * CANVAS_HEIGHT);
  }
  context.restore();
}

export function ReviewAnnotationCanvas({ annotation, editable = false, onChange }: ReviewAnnotationCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [tool, setTool] = useState<Tool>("pen");
  const [draft, setDraft] = useState<AnnotationOperation | null>(null);

  const redraw = useCallback((nextDraft: AnnotationOperation | null = draft) => {
    const context = canvasRef.current?.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
    annotation.operations.forEach((operation) => drawOperation(context, operation));
    if (nextDraft) drawOperation(context, nextDraft);
  }, [annotation.operations, draft]);

  useEffect(() => { redraw(); }, [redraw]);

  const pointFor = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) };
  };
  const commit = (operation: AnnotationOperation) => onChange?.({ ...annotation, operations: [...annotation.operations, operation] });

  return (
    <div className="pointer-events-none absolute inset-0">
      {editable && <div className="pointer-events-auto absolute left-3 top-3 z-10 flex gap-1 rounded-lg bg-zinc-950/80 p-1 text-xs text-white backdrop-blur">
        {(["pen", "circle", "text"] as Tool[]).map((item) => <button key={item} onClick={() => setTool(item)} className={`rounded px-2 py-1 ${tool === item ? "bg-red-500" : "hover:bg-zinc-700"}`}>{item === "pen" ? "畫筆" : item === "circle" ? "紅圈" : "文字"}</button>)}
        <button onClick={() => onChange?.({ ...annotation, operations: [] })} className="rounded px-2 py-1 hover:bg-zinc-700">清除</button>
      </div>}
      <canvas
        ref={canvasRef} width={CANVAS_WIDTH} height={CANVAS_HEIGHT}
        className={`h-full w-full ${editable ? "pointer-events-auto cursor-crosshair" : "pointer-events-none"}`}
        onPointerDown={(event) => {
          if (!editable) return;
          const point = pointFor(event);
          event.currentTarget.setPointerCapture(event.pointerId);
          if (tool === "text") {
            const text = window.prompt("新增畫面文字批註");
            if (text?.trim()) commit({ kind: "text", color: RED, fontSize: 26, ...point, text: text.trim() });
            return;
          }
          const operation: AnnotationOperation = tool === "pen"
            ? { kind: "stroke", color: RED, width: 5, points: [point] }
            : { kind: "circle", color: RED, width: 5, start: point, end: point };
          setDraft(operation);
          redraw(operation);
        }}
        onPointerMove={(event) => {
          if (!editable || !draft || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
          const point = pointFor(event);
          const next = draft.kind === "stroke" ? { ...draft, points: [...draft.points, point] } : { ...draft, end: point };
          setDraft(next);
          redraw(next);
        }}
        onPointerUp={(event) => {
          if (!editable || !draft) return;
          event.currentTarget.releasePointerCapture(event.pointerId);
          if (draft.kind !== "stroke" || draft.points.length > 1) commit(draft);
          setDraft(null);
        }}
      />
    </div>
  );
}
