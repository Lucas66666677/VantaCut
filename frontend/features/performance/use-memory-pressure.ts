"use client";

import { useEffect } from "react";

import { useEditorPerformanceStore } from "@/features/performance/editor-performance-store";

interface PerformanceMemory extends Performance {
  memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number };
}

/**
 * Chromium exposes `performance.memory`; other browsers return `null` rather
 * than guessing. Consumers stay at normal quality unless a real pressure signal
 * arrives. This hook must be mounted once at the editor workspace boundary.
 */
export function useMemoryPressure(pollMs = 2_000): void {
  const setMemoryPressure = useEditorPerformanceStore((state) => state.setMemoryPressure);

  useEffect(() => {
    const check = () => {
      const memory = (performance as PerformanceMemory).memory;
      const ratio = memory?.jsHeapSizeLimit ? memory.usedJSHeapSize / memory.jsHeapSizeLimit : null;
      setMemoryPressure(ratio);
    };
    check();
    const interval = window.setInterval(check, pollMs);
    return () => window.clearInterval(interval);
  }, [pollMs, setMemoryPressure]);
}
