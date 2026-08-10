"use client";

import { useCallback, useRef, useState } from "react";

import { OnScreenControls } from "@/features/editor/on-screen-controls";
import type { OscTransform } from "@/features/editor/osc-geometry";
import { applySafeZoneMagnet, collisionsForElement, type SocialPlatform } from "@/features/editor/social-safe-zones";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface TimelineSticker {
  id: string;
  asset_url: string;
  fallback_emoji: string;
  label: string;
  source_start: number;
  source_end: number;
  position: { x: number; y: number };
  scale: number;
  rotation: number;
  source: "ai" | "user";
  enabled: boolean;
  trigger?: { text: string; emotion: string };
  confidence_score?: number;
}

function StickerNode({ sticker, selected, onSelect, onCommit, broken, onBroken }: { sticker: TimelineSticker; selected: boolean; onSelect: () => void; onCommit: (transform: OscTransform) => void; broken: boolean; onBroken: () => void }) {
  const elementRef = useRef<HTMLButtonElement>(null);
  const target: OscTransform = { x: sticker.position.x, y: sticker.position.y, width: .11, height: .11, scale: sticker.scale, rotation: sticker.rotation };
  const applyPreview = useCallback((next: OscTransform) => {
    const element = elementRef.current; if (!element) return;
    // Preview updates bypass React entirely; persistence only happens when the pointer is released.
    element.style.left = `${next.x * 100}%`; element.style.top = `${next.y * 100}%`;
    element.style.transform = `translate(-50%, -50%) scale(${next.scale}) rotate(${next.rotation}deg)`;
  }, []);
  return <div>
    <button ref={elementRef} type="button" className={`pointer-events-auto absolute grid h-16 w-16 touch-none place-items-center rounded-lg transition-shadow ${selected ? "ring-2 ring-cyan-300 shadow-[0_0_18px_rgba(103,232,249,.65)]" : ""}`} style={{ left: `${sticker.position.x * 100}%`, top: `${sticker.position.y * 100}%`, transform: `translate(-50%, -50%) scale(${sticker.scale}) rotate(${sticker.rotation}deg)` }} onPointerDown={(event) => { event.stopPropagation(); onSelect(); }} title={`${sticker.label} · ${sticker.trigger?.text ?? "AI 建議"}`}>
      {broken ? <span className="text-4xl drop-shadow-lg">{sticker.fallback_emoji}</span> : <img src={sticker.asset_url.startsWith("/") ? `${API_URL}${sticker.asset_url}` : sticker.asset_url} alt={sticker.label} draggable={false} onError={onBroken} className="h-full w-full object-contain drop-shadow-lg" />}
    </button>
    {selected && <OnScreenControls target={target} onPreview={applyPreview} onCommit={onCommit} />}
  </div>;
}

interface StickerCanvasOverlayProps {
  stickers: TimelineSticker[];
  currentTime: number;
  enabled: boolean;
  onChange: (sticker: TimelineSticker) => void;
  safeZonePlatform?: SocialPlatform;
  safeZoneEnabled?: boolean;
}

/** Place inside the same `relative` wrapper as the editor's video/canvas preview. */
export function StickerCanvasOverlay({ stickers, currentTime, enabled, onChange, safeZonePlatform, safeZoneEnabled = false }: StickerCanvasOverlayProps) {
  const container = useRef<HTMLDivElement>(null); const [selected, setSelected] = useState<string | null>(null); const [brokenImages, setBrokenImages] = useState<Set<string>>(new Set()); const [colliding, setColliding] = useState<Set<string>>(new Set());
  const visible = enabled ? stickers.filter((item) => item.enabled && currentTime >= item.source_start && currentTime <= item.source_end) : [];
  const commit = (sticker: TimelineSticker, transform: OscTransform) => {
    const rect = container.current?.getBoundingClientRect(); if (!rect) return;
    const raw = { x: Math.min(.96, Math.max(.04, transform.x)), y: Math.min(.96, Math.max(.04, transform.y)), width: Math.min(.42, 64 * transform.scale / rect.width), height: Math.min(.42, 64 * transform.scale / rect.height) };
    const hits = safeZoneEnabled && safeZonePlatform ? collisionsForElement(raw, safeZonePlatform) : [];
    setColliding((current) => { const next = new Set(current); hits.length ? next.add(sticker.id) : next.delete(sticker.id); return next; });
    const snapped = safeZoneEnabled && safeZonePlatform ? applySafeZoneMagnet(raw, safeZonePlatform) : raw;
    onChange({ ...sticker, position: { x: snapped.x, y: snapped.y }, scale: transform.scale, rotation: transform.rotation });
  };
  return <div ref={container} className="pointer-events-none absolute inset-0 z-30 overflow-hidden" aria-label="貼紙預覽畫布">
    {visible.map((sticker) => <div key={sticker.id} className={colliding.has(sticker.id) ? "drop-shadow-[0_0_12px_rgba(251,113,133,.9)]" : ""}><StickerNode sticker={sticker} selected={selected === sticker.id} onSelect={() => setSelected(sticker.id)} onCommit={(transform) => commit(sticker, transform)} broken={brokenImages.has(sticker.id)} onBroken={() => setBrokenImages((current) => new Set(current).add(sticker.id))} />{colliding.has(sticker.id) && <span className="pointer-events-none absolute mt-1 -translate-x-1/2 rounded bg-rose-950/90 px-1.5 py-1 text-[10px] text-rose-100" style={{ left: `${sticker.position.x * 100}%`, top: `calc(${sticker.position.y * 100}% + 38px)` }}>平台 UI 可能遮擋</span>}</div>)}
  </div>;
}
