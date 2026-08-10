"use client";

import { useEffect, useRef } from "react";

import { KineticCaptionCanvas, type KineticCaptionCue, type CaptionVisualStyle } from "@/features/captions/kinetic-caption-canvas";

export interface BilingualCaptionCue extends KineticCaptionCue {
  target_text: string;
}

interface BilingualCaptionCanvasProps {
  cues: BilingualCaptionCue[];
  currentTimeMs: number;
  width: number;
  height: number;
  stylePreset?: CaptionVisualStyle;
  className?: string;
}

/** Main subtitle keeps its kinetic effect; translated line stays stable for fast reading. */
export function BilingualCaptionCanvas({ cues, currentTimeMs, width, height, stylePreset = "viral_yellow", className }: BilingualCaptionCanvasProps) {
  const translationCanvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = translationCanvas.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    canvas.width = width; canvas.height = height;
    context.clearRect(0, 0, width, height);
    const now = currentTimeMs / 1000;
    const cue = cues.find((item) => item.start_time <= now && now <= item.end_time);
    if (!cue?.target_text) return;
    context.save();
    context.font = `600 ${Math.max(13, Math.round(width * .052))}px Inter, Arial, sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.lineJoin = "round";
    context.lineWidth = Math.max(2, Math.round(width * .006));
    context.strokeStyle = "rgba(8, 10, 18, .9)";
    context.fillStyle = "#ffffff";
    const y = height * .82;
    context.strokeText(cue.target_text, width / 2, y);
    context.fillText(cue.target_text, width / 2, y);
    context.restore();
  }, [cues, currentTimeMs, height, width]);

  return <>
    <KineticCaptionCanvas cues={cues} currentTimeMs={currentTimeMs} width={width} height={height} stylePreset={stylePreset} className={className} />
    <canvas ref={translationCanvas} width={width} height={height} className="pointer-events-none absolute inset-0 h-full w-full" />
  </>;
}
