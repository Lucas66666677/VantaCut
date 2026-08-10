"use client";

import { useEffect, useRef, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";

export interface WebCodecsTransport {
  seekTo(timeMs: number): Promise<void> | void;
  play(playbackRate?: number): void;
  pause(): void;
  subscribeTime?(listener: (timeMs: number) => void): () => void;
}

let activeTransport: WebCodecsTransport | null = null;

/** The preview surface registers its worker-backed transport without coupling it to editor React state. */
export function registerWebCodecsTransport(transport: WebCodecsTransport): () => void {
  activeTransport = transport;
  return () => { if (activeTransport === transport) activeTransport = null; };
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable='true'], [role='textbox'], [role='dialog']"));
}

type TransportState = { direction: -1 | 0 | 1; rate: number };

/**
 * Premiere-style keyboard transport. It deliberately uses imperative refs and the
 * timeline's transient Zustand setter: J/K/L responds before any React render.
 */
export function JklKeyboardManager({ frameRate = 30, onSave }: { frameRate?: number; onSave?: () => void }) {
  const stateRef = useRef<TransportState>({ direction: 0, rate: 1 });
  const kHeldRef = useRef(false);
  const lastPressRef = useRef<{ key: "j" | "l"; at: number } | null>(null);
  const fallbackFrameRef = useRef<number | null>(null);
  const fallbackLastTimeRef = useRef<number | null>(null);
  const unsubscribeTimeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const stopFallback = () => { if (fallbackFrameRef.current !== null) cancelAnimationFrame(fallbackFrameRef.current); fallbackFrameRef.current = null; fallbackLastTimeRef.current = null; };
    const stop = () => {
      activeTransport?.pause(); unsubscribeTimeRef.current?.(); unsubscribeTimeRef.current = null;
      stopFallback(); stateRef.current = { direction: 0, rate: 1 };
    };
    const setTime = (milliseconds: number) => useTimelineStore.getState().setPlayheadTime(Math.max(0, milliseconds / 1_000));
    const startFallback = (direction: -1 | 1, rate: number) => {
      stopFallback();
      const tick = (now: number) => {
        if (stateRef.current.direction !== direction) return;
        const elapsed = now - (fallbackLastTimeRef.current ?? now); fallbackLastTimeRef.current = now;
        const next = Math.max(0, useTimelineStore.getState().playheadTime + direction * rate * elapsed / 1_000);
        useTimelineStore.getState().setPlayheadTime(next); fallbackFrameRef.current = requestAnimationFrame(tick);
      };
      fallbackFrameRef.current = requestAnimationFrame(tick);
    };
    const start = (direction: -1 | 1, rate: number) => {
      unsubscribeTimeRef.current?.(); unsubscribeTimeRef.current = null;
      const transport = activeTransport;
      if (!transport) { startFallback(direction, rate); return; }
      transport.play(direction * rate);
      if (transport.subscribeTime) unsubscribeTimeRef.current = transport.subscribeTime(setTime);
      else startFallback(direction, rate);
    };
    const step = (direction: -1 | 1) => {
      stop();
      const next = Math.max(0, useTimelineStore.getState().playheadTime + direction / frameRate);
      useTimelineStore.getState().setPlayheadTime(next);
      // Exact frame stepping goes straight to the worker-backed WebCodecs seek command.
      void activeTransport?.seekTo(Math.round(next * 1_000));
    };
    const keyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && key === "s") { event.preventDefault(); onSave?.(); window.dispatchEvent(new CustomEvent("editor-project-save")); return; }
      if (isEditableTarget(event.target) || modifier || event.altKey || event.defaultPrevented) return;
      if (key === "k") { event.preventDefault(); kHeldRef.current = true; if (!event.repeat) stop(); return; }
      if (key !== "j" && key !== "l" || event.repeat) return;
      event.preventDefault();
      const direction: -1 | 1 = key === "j" ? -1 : 1;
      if (kHeldRef.current) { step(direction); return; }
      const now = performance.now(); const previous = lastPressRef.current;
      const repeatDirection = previous?.key === key && now - previous.at <= 520 && stateRef.current.direction === direction;
      const rate = repeatDirection ? Math.min(8, stateRef.current.rate * 2) : 1;
      lastPressRef.current = { key, at: now }; stateRef.current = { direction, rate };
      start(direction, rate);
    };
    const keyUp = (event: KeyboardEvent) => { if (event.key.toLowerCase() === "k") kHeldRef.current = false; };
    window.addEventListener("keydown", keyDown, { capture: true }); window.addEventListener("keyup", keyUp, { capture: true });
    return () => { window.removeEventListener("keydown", keyDown, { capture: true }); window.removeEventListener("keyup", keyUp, { capture: true }); stop(); };
  }, [frameRate, onSave]);
  return null;
}

/** Small non-blocking status chip; DOM state changes are intentionally infrequent (only key presses). */
export function JklShortcutHint() {
  const [message, setMessage] = useState("J / K / L 導覽 · K+J/L 逐影格");
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return;
      if (event.key.toLowerCase() === "k") setMessage("暫停 · 按住 K + J/L 逐影格");
      if (event.key.toLowerCase() === "j") setMessage("J 倒轉播放");
      if (event.key.toLowerCase() === "l") setMessage("L 正轉播放（連按加速）");
    };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, []);
  return <p className="text-[10px] text-zinc-500" aria-live="polite">{message}</p>;
}
