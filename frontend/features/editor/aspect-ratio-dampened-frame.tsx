"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** Fixed internal 16:9 coordinate system that uses scale() during panel resizing, preventing canvas distortion. */
export function AspectRatioDampenedFrame({ children, className = "", baseWidth = 960 }: { children: ReactNode; className?: string; baseWidth?: number }) {
  const hostRef = useRef<HTMLDivElement>(null); const frameRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = hostRef.current; const frame = frameRef.current; if (!host || !frame) return;
    const baseHeight = baseWidth * 9 / 16;
    let current = 1; let target = 1; let raf = 0;
    const settle = () => {
      current += (target - current) * .28;
      frame.style.transform = `translate3d(-50%, -50%, 0) scale(${current})`;
      if (Math.abs(target - current) > .001) raf = requestAnimationFrame(settle);
      else frame.style.transform = `translate3d(-50%, -50%, 0) scale(${target})`;
    };
    const measure = () => {
      const width = host.clientWidth; const height = host.clientHeight;
      target = Math.max(.05, Math.min(width / baseWidth, height / baseHeight));
      cancelAnimationFrame(raf); raf = requestAnimationFrame(settle);
    };
    const observer = new ResizeObserver(measure); observer.observe(host); measure();
    return () => { cancelAnimationFrame(raf); observer.disconnect(); };
  }, [baseWidth]);
  return <div ref={hostRef} className={`relative h-full w-full overflow-hidden ${className}`}><div ref={frameRef} className="absolute left-1/2 top-1/2 aspect-video origin-center will-change-transform" style={{ width: baseWidth, height: baseWidth * 9 / 16 }}>{children}</div></div>;
}
