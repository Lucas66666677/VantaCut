export type SocialPlatform = "tiktok" | "instagram_reels" | "youtube_shorts";

export interface NormalizedRect { id: string; label: string; x: number; y: number; width: number; height: number; }
export interface SocialSafeZonePreset { id: SocialPlatform; label: string; description: string; unsafeRects: NormalizedRect[]; }
export interface OverlayElementBounds { x: number; y: number; width: number; height: number; }

/**
 * Conservative 9:16 UI reservations. Platforms can change their chrome by locale, device,
 * caption length, and ad add-ons, so these are deliberately maintained as versioned presets.
 */
export const SOCIAL_SAFE_ZONE_PRESETS: Record<SocialPlatform, SocialSafeZonePreset> = {
  tiktok: {
    id: "tiktok", label: "TikTok", description: "右側互動列與底部帳號／文案區",
    unsafeRects: [
      { id: "top", label: "頂部系統資訊", x: 0, y: 0, width: 1, height: .105 },
      { id: "actions", label: "點讚／留言／分享", x: .795, y: .245, width: .205, height: .49 },
      { id: "caption", label: "帳號／文案／音樂", x: 0, y: .755, width: .93, height: .245 },
    ],
  },
  instagram_reels: {
    id: "instagram_reels", label: "Instagram Reels", description: "右側互動列與底部說明／音樂區",
    unsafeRects: [
      { id: "top", label: "頂部導覽", x: 0, y: 0, width: 1, height: .095 },
      { id: "actions", label: "頭像／按讚／留言／分享", x: .79, y: .27, width: .21, height: .52 },
      { id: "caption", label: "帳號／說明／音樂", x: 0, y: .77, width: .92, height: .23 },
    ],
  },
  youtube_shorts: {
    id: "youtube_shorts", label: "YouTube Shorts", description: "右側按鈕列與底部標題／頻道區",
    unsafeRects: [
      { id: "top", label: "頂部導覽", x: 0, y: 0, width: 1, height: .09 },
      { id: "actions", label: "喜歡／留言／分享／訂閱", x: .80, y: .24, width: .20, height: .54 },
      { id: "caption", label: "標題／頻道／描述", x: 0, y: .79, width: .94, height: .21 },
    ],
  },
};

export const isRectCollision = (first: OverlayElementBounds, second: NormalizedRect) =>
  first.x - first.width / 2 < second.x + second.width
  && first.x + first.width / 2 > second.x
  && first.y - first.height / 2 < second.y + second.height
  && first.y + first.height / 2 > second.y;

export function collisionsForElement(element: OverlayElementBounds, platform: SocialPlatform): NormalizedRect[] {
  return SOCIAL_SAFE_ZONE_PRESETS[platform].unsafeRects.filter((rect) => isRectCollision(element, rect));
}

/** Pull an element part way toward the nearest edge; retaining some overlap makes the warning visible. */
export function applySafeZoneMagnet(element: OverlayElementBounds, platform: SocialPlatform, resistance = .42): OverlayElementBounds {
  const collision = collisionsForElement(element, platform)[0];
  if (!collision) return element;
  const candidates = [
    { axis: "x" as const, value: collision.x - element.width / 2 - .012 },
    { axis: "x" as const, value: collision.x + collision.width + element.width / 2 + .012 },
    { axis: "y" as const, value: collision.y - element.height / 2 - .012 },
    { axis: "y" as const, value: collision.y + collision.height + element.height / 2 + .012 },
  ].map((item) => ({ ...item, distance: Math.abs((item.axis === "x" ? element.x : element.y) - item.value) }));
  const nearest = candidates.sort((a, b) => a.distance - b.distance)[0];
  const value = (nearest.axis === "x" ? element.x : element.y) * (1 - resistance) + nearest.value * resistance;
  return nearest.axis === "x" ? { ...element, x: Math.max(.04, Math.min(.96, value)) } : { ...element, y: Math.max(.04, Math.min(.96, value)) };
}
