"use client";

import { useEffect, useRef, useState } from "react";

import { useSlipSlideToolStore } from "@/features/editor/slip-slide-editing";

interface GhostFramePlayer {
  requestFrame: (assetId: string, timeMs: number) => Promise<ImageBitmap | null>;
  loadProxySegment: (assetId: string, startMs: number, endMs: number) => void;
}

function GhostFrame({ bitmap, label, timeMs }: { bitmap: ImageBitmap | null; label: string; timeMs: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas || !bitmap) return;
    const context = canvas.getContext("2d"); if (!context) return;
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  }, [bitmap]);
  return <div className="relative min-w-0 overflow-hidden rounded-lg border border-white/20 bg-black shadow-2xl">
    <canvas ref={canvasRef} className="block aspect-video h-full w-full object-contain" />
    {!bitmap && <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(circle_at_50%_30%,#334155,#020617)] text-xs text-zinc-300">讀取幽靈影格…</div>}
    <div className="absolute inset-x-0 bottom-0 flex justify-between bg-black/70 px-2 py-1 text-[10px] font-semibold text-white"><span>{label}</span><span>{(timeMs / 1_000).toFixed(3)}s</span></div>
  </div>;
}

/** Preview-only overlay. The two frames are pulled from the Worker LRU without seeking the main program monitor. */
export function SlipGhostFrameSplitView({ requestFrame, loadProxySegment }: GhostFramePlayer) {
  const preview = useSlipSlideToolStore((state) => state.ghostPreview);
  const [frames, setFrames] = useState<{ inFrame: ImageBitmap | null; outFrame: ImageBitmap | null }>({ inFrame: null, outFrame: null });

  useEffect(() => {
    let cancelled = false;
    if (!preview?.sourceAssetId) { setFrames({ inFrame: null, outFrame: null }); return; }
    loadProxySegment(preview.sourceAssetId, Math.max(0, preview.inTimeMs - 1_000), preview.outTimeMs + 1_000);
    const read = async () => {
      // Decoders may need one microtask to deposit a newly requested keyframe in the LRU.
      await new Promise((resolve) => window.setTimeout(resolve, 32));
      const [inFrame, outFrame] = await Promise.all([requestFrame(preview.sourceAssetId!, preview.inTimeMs), requestFrame(preview.sourceAssetId!, Math.max(preview.inTimeMs, preview.outTimeMs - 33))]);
      if (cancelled) { inFrame?.close(); outFrame?.close(); return; }
      setFrames((previous) => { previous.inFrame?.close(); previous.outFrame?.close(); return { inFrame, outFrame }; });
    };
    void read();
    return () => { cancelled = true; };
  }, [loadProxySegment, preview?.inTimeMs, preview?.outTimeMs, preview?.sourceAssetId, requestFrame]);

  useEffect(() => () => { frames.inFrame?.close(); frames.outFrame?.close(); }, [frames]);
  if (!preview) return null;
  return <div className="pointer-events-none absolute inset-x-5 top-5 z-30 grid grid-cols-2 gap-1 rounded-xl border border-cyan-100/35 bg-zinc-950/55 p-1.5 backdrop-blur-md">
    <GhostFrame bitmap={frames.inFrame} label="Slip · 新 In" timeMs={preview.inTimeMs} />
    <GhostFrame bitmap={frames.outFrame} label="Slip · 新 Out" timeMs={preview.outTimeMs} />
  </div>;
}
