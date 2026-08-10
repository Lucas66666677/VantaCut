"use client";

interface PreviewBufferingOverlayProps {
  active: boolean;
  assetIds?: string[];
}

/** Overlay this inside the same relative container as the preview canvas. */
export function PreviewBufferingOverlay({ active, assetIds = [] }: PreviewBufferingOverlayProps) {
  if (!active) return null;
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center bg-slate-950/5 backdrop-blur-[1.5px] transition-opacity duration-150" aria-live="polite" aria-label="正在緩衝預覽影格">
      <div className="flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-3 py-1.5 text-[11px] text-white shadow-lg shadow-black/20 backdrop-blur-xl">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-cyan-200" />
        <span>正在精準定位影格{assetIds.length ? "…" : ""}</span>
      </div>
    </div>
  );
}
