"use client";

import { useEffect, useRef, useState } from "react";

import { drawCubeLut, parseCubeLut } from "@/features/editor/lut-webgl";

interface BeforeAfterColorMatchProps {
  previewSource: CanvasImageSource | null;
  lutUrl: string | null;
  frameVersion?: number;
  intensity?: number;
}

export function BeforeAfterColorMatch({ previewSource, lutUrl, frameVersion = 0, intensity = 100 }: BeforeAfterColorMatchProps) {
  const beforeRef = useRef<HTMLCanvasElement>(null); const afterRef = useRef<HTMLCanvasElement>(null);
  const [split, setSplit] = useState(50); const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const render = async () => {
      if (!previewSource || !lutUrl || !beforeRef.current || !afterRef.current) return;
      try {
        const cube = parseCubeLut(await (await fetch(lutUrl)).text());
        if (cancelled || !beforeRef.current || !afterRef.current) return;
        const before = beforeRef.current.getContext("2d");
        before?.clearRect(0, 0, beforeRef.current.width, beforeRef.current.height);
        before?.drawImage(previewSource, 0, 0, beforeRef.current.width, beforeRef.current.height);
        drawCubeLut(afterRef.current, previewSource, cube, intensity / 100); setError(null);
      } catch (cause) { if (!cancelled) setError(cause instanceof Error ? cause.message : "無法產生色彩預覽"); }
    };
    void render(); return () => { cancelled = true; };
  }, [frameVersion, intensity, lutUrl, previewSource]);
  if (!lutUrl) return null;
  return <div className="mt-3"><div className="relative aspect-video overflow-hidden rounded-lg bg-black"><canvas ref={beforeRef} width={640} height={360} className="absolute inset-0 h-full w-full" aria-label="原始畫面" /><canvas ref={afterRef} width={640} height={360} className="absolute inset-0 h-full w-full" style={{ clipPath: `inset(0 0 0 ${split}%)` }} aria-label="套用色彩匹配後畫面" /><div className="pointer-events-none absolute bottom-0 top-0 z-10 w-0.5 bg-white shadow-[0_0_0_1px_rgba(0,0,0,.75)]" style={{ left: `${split}%` }}><span className="absolute -left-8 top-2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-white">Before</span><span className="absolute left-2 top-2 rounded bg-fuchsia-500/90 px-1.5 py-0.5 text-[10px] text-white">After</span></div><input aria-label="Before and after comparison" type="range" min="0" max="100" value={split} onChange={(event) => setSplit(Number(event.target.value))} className="absolute inset-0 z-20 h-full w-full cursor-ew-resize opacity-0" /></div><p className="mt-1 text-xs text-zinc-400">拖曳中線比較原始畫面與參考色調。</p>{error && <p className="mt-1 text-xs text-red-300">{error}</p>}</div>;
}
