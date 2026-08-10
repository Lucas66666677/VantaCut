"use client";

import { useEffect, useRef } from "react";

export type FitnessHudStyle = "impact" | "neon" | "minimal";
export interface FitnessRepEvent { rep: number; timeline_time: number; fatigue?: boolean; }

const palette: Record<FitnessHudStyle, { accent: string; panel: string }> = {
  impact: { accent: "#fbbf24", panel: "rgba(0,0,0,.58)" }, neon: { accent: "#22d3ee", panel: "rgba(8,47,73,.66)" }, minimal: { accent: "#ffffff", panel: "rgba(0,0,0,.42)" },
};

/** Canvas HUD preview mirrors the final FFmpeg timing without forcing a full video re-render. */
export function FitnessHudCanvas({ events, targetReps, style, currentTime, fatigueTime }: { events: FitnessRepEvent[]; targetReps: number; style: FitnessHudStyle; currentTime: number; fatigueTime?: number | null }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = canvasRef.current; const context = canvas?.getContext("2d"); if (!canvas || !context) return;
    const width = canvas.width; const height = canvas.height; const colours = palette[style]; const completed = events.filter((event) => event.timeline_time <= currentTime).length;
    const active = [...events].reverse().find((event) => currentTime >= event.timeline_time && currentTime <= event.timeline_time + .68);
    context.clearRect(0, 0, width, height); context.fillStyle = "#111827"; context.fillRect(0, 0, width, height);
    // A neutral preview plate allows the graphics to be judged without requiring a decoded video frame.
    context.fillStyle = "#1f2937"; context.fillRect(16, 16, width - 32, height - 32);
    const fatigueActive = fatigueTime !== undefined && fatigueTime !== null && currentTime >= fatigueTime && currentTime <= fatigueTime + 1;
    if (fatigueActive) { context.strokeStyle = "rgba(239,68,68,.95)"; context.lineWidth = 20; context.strokeRect(4, 4, width - 8, height - 8); }
    context.fillStyle = colours.panel; context.fillRect(width * .12, height * .79, width * .76, height * .055);
    context.fillStyle = colours.accent; context.fillRect(width * .12, height * .79, width * .76 * Math.min(1, completed / Math.max(1, targetReps)), height * .055);
    context.fillStyle = "#e5e7eb"; context.font = "bold 14px system-ui"; context.textAlign = "center"; context.fillText(`${completed} / ${targetReps} REPS`, width / 2, height * .75);
    if (active) { const phase = Math.min(1, (currentTime - active.timeline_time) / .68); const scale = 1 + Math.sin(phase * Math.PI) * .18; context.save(); context.translate(width / 2, height * .43); context.scale(scale, scale); context.fillStyle = colours.panel; context.fillRect(-62, -68, 124, 128); context.fillStyle = colours.accent; context.font = "900 96px system-ui"; context.fillText(String(active.rep), 0, 35); context.restore(); }
  }, [currentTime, events, fatigueTime, style, targetReps]);
  return <canvas ref={canvasRef} width={540} height={360} className="h-52 w-full rounded-lg border border-orange-300/20" aria-label="運動儀表板即時預覽" />;
}
