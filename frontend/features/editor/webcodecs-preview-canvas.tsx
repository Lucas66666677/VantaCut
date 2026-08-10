"use client";

import { useEffect, useRef } from "react";

import { PreviewBufferingOverlay } from "@/features/editor/preview-buffering-overlay";
import { AspectRatioDampenedFrame } from "@/features/editor/aspect-ratio-dampened-frame";
import { useSpacebarPan } from "@/features/editor/use-spacebar-pan";
import { useVideoCanvasPlayer, type CanvasPreviewClip, type CanvasPreviewSubtitle, type PreviewLut, type ProxyVideoSource } from "@/features/editor/use-video-canvas-player";
import { registerWebCodecsTransport } from "@/features/editor/jkl-keyboard-navigation";
import { SlipGhostFrameSplitView } from "@/features/editor/slip-ghost-frame-split";

interface WebCodecsPreviewCanvasProps {
  sources: ProxyVideoSource[];
  clips: CanvasPreviewClip[];
  subtitles?: CanvasPreviewSubtitle[];
  lut?: PreviewLut;
  workerDemuxerModuleUrl: string;
  className?: string;
  onPlayerReady?: (player: ReturnType<typeof useVideoCanvasPlayer>) => void;
}

/**
 * Drop-in preview surface with no HTMLVideoElement. Decoding, WebGL compositing
 * and frame caching stay in the dedicated Worker; this component only renders UI.
 */
export function WebCodecsPreviewCanvas({ sources, clips, subtitles, lut, workerDemuxerModuleUrl, className = "", onPlayerReady }: WebCodecsPreviewCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { spaceHeld, isPanning } = useSpacebarPan({ viewportRef, contentRef, transformVariable: "--preview-pan" });
  const player = useVideoCanvasPlayer({ canvasRef, sources, clips, subtitles, lut, workerDemuxerModuleUrl });
  // Expose controls without putting callbacks into the render-critical Worker channel.
  useEffect(() => { if (player.isReady) onPlayerReady?.(player); }, [onPlayerReady, player]);
  useEffect(() => player.isReady ? registerWebCodecsTransport(player) : undefined, [player, player.isReady]);

  return (
    <AspectRatioDampenedFrame className={`rounded-xl bg-black ${className}`}>
    <div ref={viewportRef} className={`relative h-full w-full overflow-hidden ${spaceHeld ? isPanning ? "cursor-grabbing" : "cursor-grab" : ""}`}>
    <div ref={contentRef} className="h-full w-full will-change-transform" style={{ transform: "var(--preview-pan, translate3d(0, 0, 0))" }}>
      <canvas
        ref={canvasRef}
        className={`block h-full w-full transition-[filter] duration-150 ${player.isBuffering ? "blur-[1.5px]" : ""}`}
        aria-label="WebCodecs 影片預覽"
      />
      <PreviewBufferingOverlay active={player.isBuffering} assetIds={player.bufferingAssetIds} />
      <SlipGhostFrameSplitView requestFrame={player.requestFrame} loadProxySegment={player.loadProxySegment} />
      {player.error && <div className="absolute bottom-2 left-2 rounded bg-red-500/85 px-2 py-1 text-[11px] text-white">預覽解碼失敗：{player.error.message}</div>}
      {!player.isSupported && <div className="absolute inset-0 grid place-items-center p-4 text-center text-xs text-zinc-300">此瀏覽器不支援 WebCodecs／OffscreenCanvas 預覽。</div>}
    </div>
    </div>
    </AspectRatioDampenedFrame>
  );
}
