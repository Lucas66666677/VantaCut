"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface AudioWaveformSource {
  id: string;
  proxyUrl: string;
  durationMs: number;
  decoderConfig: AudioDecoderConfig;
  /** Worker-only module URL exporting `demuxAudio`; never imported by the UI thread. */
  workerDemuxerModuleUrl: string;
}
export interface WaveformLod { resolutionMs: 1_000 | 100 | 10; /** Interleaved [RMS, peak] values. */ values: Float32Array; }
export interface AudioWaveformAnalysis { lods: WaveformLod[]; progress: number; loading: boolean; error: Error | null; }

export function chooseWaveformLod(lods: WaveformLod[], pixelsPerSecond: number): WaveformLod | undefined {
  const desired = pixelsPerSecond >= 120 ? 10 : pixelsPerSecond >= 12 ? 100 : 1_000;
  return lods.find((lod) => lod.resolutionMs === desired) ?? lods[0];
}

/** UI-thread controller only: decoding and PCM analysis remain inside audio-waveform.worker.ts. */
export function useAudioWaveform(source?: AudioWaveformSource): AudioWaveformAnalysis {
  const workerRef = useRef<Worker | null>(null);
  const sourceRef = useRef(source);
  const [lods, setLods] = useState<WaveformLod[]>([]);
  const [progress, setProgress] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const requestKey = useMemo(() => source ? `${source.id}-${source.proxyUrl}` : undefined, [source]);
  useEffect(() => { sourceRef.current = source; }, [source]);

  useEffect(() => {
    const activeSource = sourceRef.current;
    if (!activeSource || !requestKey) { setLods([]); return; }
    const worker = new Worker(new URL("./audio-waveform.worker.ts", import.meta.url));
    workerRef.current = worker;
    setLoading(true); setProgress(0); setError(null); setLods([]);
    worker.onmessage = (event: MessageEvent<
      | { type: "progress"; progress: number }
      | { type: "complete"; lods: Array<{ resolutionMs: WaveformLod["resolutionMs"]; values: ArrayBuffer }> }
      | { type: "error"; message: string }
    >) => {
      if (event.data.type === "progress") setProgress(event.data.progress);
      if (event.data.type === "complete") { setLods(event.data.lods.map((lod) => ({ resolutionMs: lod.resolutionMs, values: new Float32Array(lod.values) }))); setProgress(100); setLoading(false); }
      if (event.data.type === "error") { setError(new Error(event.data.message)); setLoading(false); }
    };
    worker.postMessage({ type: "analyse", ...activeSource });
    return () => { worker.postMessage({ type: "cancel", id: activeSource.id }); worker.terminate(); workerRef.current = null; };
  }, [requestKey]);

  return { lods, progress, loading, error };
}
