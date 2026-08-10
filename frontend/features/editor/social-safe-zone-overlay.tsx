"use client";

import { SOCIAL_SAFE_ZONE_PRESETS, type SocialPlatform } from "@/features/editor/social-safe-zones";

export function SocialSafeZoneOverlay({ platform, visible }: { platform: SocialPlatform; visible: boolean }) {
  if (!visible) return null;
  const preset = SOCIAL_SAFE_ZONE_PRESETS[platform];
  return <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden" aria-label={`${preset.label} 社群安全區預覽`}>
    <div className="absolute inset-[5%] rounded border border-emerald-300/70 shadow-[0_0_0_999px_rgba(0,0,0,.08)]"><span className="absolute left-1 top-1 rounded bg-emerald-400/85 px-1.5 py-0.5 text-[9px] font-bold text-emerald-950">建議安全區</span></div>
    {preset.unsafeRects.map((zone) => <div key={zone.id} className="absolute border border-dashed border-rose-200/70 bg-rose-500/20" style={{ left: `${zone.x * 100}%`, top: `${zone.y * 100}%`, width: `${zone.width * 100}%`, height: `${zone.height * 100}%` }}><span className="absolute left-1 top-1 rounded bg-rose-950/80 px-1 py-0.5 text-[9px] text-rose-100">{zone.label}</span></div>)}
  </div>;
}

export function SocialSafeZoneControls({ platform, visible, onPlatformChange, onVisibleChange }: { platform: SocialPlatform; visible: boolean; onPlatformChange: (platform: SocialPlatform) => void; onVisibleChange: (visible: boolean) => void }) {
  return <div className="pointer-events-auto absolute left-3 top-3 z-40 flex items-center gap-2 rounded-lg border border-white/15 bg-zinc-950/80 p-2 text-xs text-zinc-100 backdrop-blur"><label className="flex items-center gap-1.5"><input type="checkbox" checked={visible} onChange={(event) => onVisibleChange(event.target.checked)} className="accent-emerald-400" />安全區</label><select value={platform} disabled={!visible} onChange={(event) => onPlatformChange(event.target.value as SocialPlatform)} className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-[11px] disabled:opacity-45"><option value="tiktok">TikTok</option><option value="instagram_reels">Instagram Reels</option><option value="youtube_shorts">YouTube Shorts</option></select></div>;
}
