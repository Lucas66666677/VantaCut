"use client";

import { useState, type PointerEvent } from "react";

import { StickerCanvasOverlay, type TimelineSticker } from "@/features/editor/sticker-canvas-overlay";
import { SocialSafeZoneControls, SocialSafeZoneOverlay } from "@/features/editor/social-safe-zone-overlay";
import { IntentFloatingMenu, type CanvasPoint } from "@/features/editor/intent-floating-menu";
import type { SocialPlatform } from "@/features/editor/social-safe-zones";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface StickerPreviewStageProps { timelineId: string; userId: string; currentTime: number; children: React.ReactNode; stickers: TimelineSticker[]; enabled: boolean; onStickersChange: (stickers: TimelineSticker[]) => void; contextualPerson?: { mediaAssetId: string; frameTime: number; isPersonHit?: (point: CanvasPoint) => boolean; onBeauty?: () => void }; }

/** Wrap a Canvas/WebCodecs/video preview with draggable sticker controls and persist every completed adjustment. */
export function StickerPreviewStage({ timelineId, userId, currentTime, children, stickers, enabled, onStickersChange, contextualPerson }: StickerPreviewStageProps) {
  const [saving, setSaving] = useState(false); const [safeZoneVisible, setSafeZoneVisible] = useState(true); const [safeZonePlatform, setSafeZonePlatform] = useState<SocialPlatform>("tiktok");
  const [intentAnchor, setIntentAnchor] = useState<CanvasPoint | null>(null);
  const update = async (next: TimelineSticker) => {
    const updated = stickers.map((item) => item.id === next.id ? next : item); onStickersChange(updated); setSaving(true);
    try { await fetch(`${API_URL}/api/v1/timelines/${timelineId}/stickers/${encodeURIComponent(next.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, source: "user", transform: { x: next.position.x, y: next.position.y, scale: next.scale, rotation: next.rotation } }) }); } finally { setSaving(false); }
  };
  const openIntentMenu = (event: PointerEvent<HTMLDivElement>) => {
    if (!contextualPerson || event.button !== 0) return;
    if ((event.target as HTMLElement).closest("[data-intent-menu]")) return;
    const rect = event.currentTarget.getBoundingClientRect(); const point = { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
    if (contextualPerson.isPersonHit?.(point) ?? true) setIntentAnchor(point); else setIntentAnchor(null);
  };
  return <div onPointerDownCapture={openIntentMenu} className="relative aspect-video overflow-hidden rounded-xl bg-black">{children}<SocialSafeZoneOverlay platform={safeZonePlatform} visible={safeZoneVisible} /><StickerCanvasOverlay stickers={stickers} currentTime={currentTime} enabled={enabled} onChange={(item) => void update(item)} safeZonePlatform={safeZonePlatform} safeZoneEnabled={safeZoneVisible} /><SocialSafeZoneControls platform={safeZonePlatform} visible={safeZoneVisible} onPlatformChange={setSafeZonePlatform} onVisibleChange={setSafeZoneVisible} />{intentAnchor && contextualPerson && <IntentFloatingMenu anchor={intentAnchor} mediaAssetId={contextualPerson.mediaAssetId} timelineId={timelineId} userId={userId} frameTime={contextualPerson.frameTime} onBeauty={contextualPerson.onBeauty} onClose={() => setIntentAnchor(null)} />}{saving && <span className="absolute bottom-2 right-2 rounded bg-black/70 px-2 py-1 text-[10px] text-white">儲存貼紙位置…</span>}</div>;
}
