"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

interface SpacebarPanOptions {
  viewportRef: RefObject<HTMLElement | null>;
  contentRef: RefObject<HTMLElement | null>;
  /** Composes safely with zoom transforms through a CSS variable. */
  transformVariable?: "--editor-pan" | "--preview-pan";
}

/** Figma-style Space + drag: only a composited translate3d changes, never layout dimensions. */
export function useSpacebarPan({ viewportRef, contentRef, transformVariable = "--editor-pan" }: SpacebarPanOptions) {
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [isPanning, setPanning] = useState(false);
  const heldRef = useRef(false); const draggingRef = useRef<{ pointerId: number; x: number; y: number; startX: number; startY: number } | null>(null);
  const offsetRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const canHandle = () => !((document.activeElement as HTMLElement | null)?.closest("input, textarea, [contenteditable='true']"));
    const keyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !canHandle()) return;
      event.preventDefault(); heldRef.current = true; setSpaceHeld(true); viewportRef.current?.setAttribute("data-space-panning", "true");
    };
    const keyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      heldRef.current = false; setSpaceHeld(false); viewportRef.current?.removeAttribute("data-space-panning");
    };
    window.addEventListener("keydown", keyDown); window.addEventListener("keyup", keyUp);
    return () => { window.removeEventListener("keydown", keyDown); window.removeEventListener("keyup", keyUp); };
  }, [viewportRef]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const down = (event: PointerEvent) => {
      if (!heldRef.current || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      draggingRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, startX: offsetRef.current.x, startY: offsetRef.current.y };
      viewport.setPointerCapture(event.pointerId); viewport.setAttribute("data-space-dragging", "true"); setPanning(true);
    };
    const move = (event: PointerEvent) => {
      const drag = draggingRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const next = { x: drag.startX + event.clientX - drag.x, y: drag.startY + event.clientY - drag.y };
      offsetRef.current = next;
      // DOM mutation is intentionally direct: no React render or Layout Reflow during drag.
      contentRef.current?.style.setProperty(transformVariable, `translate3d(${next.x}px, ${next.y}px, 0)`);
    };
    const release = (event: PointerEvent) => {
      if (draggingRef.current?.pointerId !== event.pointerId) return;
      draggingRef.current = null; if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      viewport.removeAttribute("data-space-dragging"); setPanning(false);
    };
    viewport.addEventListener("pointerdown", down, true); viewport.addEventListener("pointermove", move, true); viewport.addEventListener("pointerup", release, true); viewport.addEventListener("pointercancel", release, true);
    return () => { viewport.removeEventListener("pointerdown", down, true); viewport.removeEventListener("pointermove", move, true); viewport.removeEventListener("pointerup", release, true); viewport.removeEventListener("pointercancel", release, true); };
  }, [contentRef, transformVariable, viewportRef]);

  return { spaceHeld, isPanning, resetPan: () => { offsetRef.current = { x: 0, y: 0 }; contentRef.current?.style.setProperty(transformVariable, "translate3d(0, 0, 0)"); } };
}
