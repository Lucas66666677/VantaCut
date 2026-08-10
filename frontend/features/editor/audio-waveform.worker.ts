/// <reference lib="webworker" />

export {};

type EncodedAudioChunkPayload = { type: EncodedAudioChunkType; timestamp: number; duration?: number; data: ArrayBuffer };
type WaveformLodPayload = { resolutionMs: number; values: ArrayBuffer };
type AnalyseRequest = {
  type: "analyse";
  id: string;
  proxyUrl: string;
  durationMs: number;
  decoderConfig: AudioDecoderConfig;
  /** A worker-only module exporting demuxAudio({ proxyUrl, startUs, endUs }). */
  demuxerModuleUrl: string;
  chunkDurationMs?: number;
};

const LOD_RESOLUTIONS_MS = [1_000, 100, 10] as const;
const ANALYSIS_SAMPLE_RATE = 16_000;

type Bin = { energy: number; peak: number; samples: number };

/** Stores just RMS/peak aggregates; decoded PCM is discarded after each small segment. */
class WaveformAccumulator {
  private readonly bins: Map<number, Bin>[];
  constructor(private readonly durationMs: number) {
    this.bins = LOD_RESOLUTIONS_MS.map(() => new Map<number, Bin>());
  }

  add(samples: Float32Array, timestampUs: number, sampleRate: number): void {
    const startMs = timestampUs / 1_000;
    for (let sampleIndex = 0; sampleIndex < samples.length; sampleIndex += 1) {
      const value = samples[sampleIndex] ?? 0;
      const absolute = Math.abs(value);
      const timeMs = startMs + sampleIndex * 1_000 / sampleRate;
      for (let level = 0; level < LOD_RESOLUTIONS_MS.length; level += 1) {
        const resolution = LOD_RESOLUTIONS_MS[level]!;
        const index = Math.max(0, Math.floor(timeMs / resolution));
        const bins = this.bins[level]!;
        const bin = bins.get(index) ?? { energy: 0, peak: 0, samples: 0 };
        bin.energy += value * value;
        bin.peak = Math.max(bin.peak, absolute);
        bin.samples += 1;
        bins.set(index, bin);
      }
    }
  }

  toPayload(): WaveformLodPayload[] {
    return LOD_RESOLUTIONS_MS.map((resolution, level) => {
      const count = Math.max(1, Math.ceil(this.durationMs / resolution));
      // Interleaved [rms, peak], so the renderer uploads a compact RG float vertex buffer.
      const values = new Float32Array(count * 2);
      for (const [index, bin] of this.bins[level]!) {
        values[index * 2] = Math.sqrt(bin.energy / Math.max(1, bin.samples));
        values[index * 2 + 1] = bin.peak;
      }
      return { resolutionMs: resolution, values: values.buffer };
    });
  }
}

async function monoAnalysisSamples(frame: AudioData): Promise<{ samples: Float32Array; sampleRate: number }> {
  const source = new Float32Array(frame.numberOfFrames);
  frame.copyTo(source, { planeIndex: 0, format: "f32-planar" });
  // Normalizing each short decoded piece with OfflineAudioContext keeps analysis stable
  // across source sample rates without ever constructing an AudioBuffer for the whole song.
  if (typeof OfflineAudioContext === "undefined" || frame.sampleRate === ANALYSIS_SAMPLE_RATE) return { samples: source, sampleRate: frame.sampleRate };
  const outputFrames = Math.max(1, Math.ceil(source.length * ANALYSIS_SAMPLE_RATE / frame.sampleRate));
  const context = new OfflineAudioContext(1, outputFrames, ANALYSIS_SAMPLE_RATE);
  const buffer = context.createBuffer(1, source.length, frame.sampleRate);
  buffer.copyToChannel(source, 0);
  const node = context.createBufferSource();
  node.buffer = buffer;
  node.connect(context.destination);
  node.start();
  const rendered = await context.startRendering();
  return { samples: rendered.getChannelData(0).slice(), sampleRate: ANALYSIS_SAMPLE_RATE };
}

async function analyse(request: AnalyseRequest, cancelled: Set<string>): Promise<void> {
  const module = await import(/* webpackIgnore: true */ request.demuxerModuleUrl) as {
    demuxAudio: (options: { proxyUrl: string; startUs: number; endUs: number }) => AsyncIterable<EncodedAudioChunkPayload>;
  };
  const accumulator = new WaveformAccumulator(request.durationMs);
  let queuedAnalysis = Promise.resolve();
  const decoder = new AudioDecoder({
    output: (frame) => {
      queuedAnalysis = queuedAnalysis.then(async () => {
        try {
          const analysis = await monoAnalysisSamples(frame);
          accumulator.add(analysis.samples, frame.timestamp, analysis.sampleRate);
        } finally {
          frame.close();
        }
      });
    },
    error: (error) => workerScope.postMessage({ type: "error", id: request.id, message: error.message }),
  });
  decoder.configure(request.decoderConfig);
  const chunkDurationMs = request.chunkDurationMs ?? 8_000;
  try {
    for (let startMs = 0; startMs < request.durationMs && !cancelled.has(request.id); startMs += chunkDurationMs) {
      const endMs = Math.min(request.durationMs, startMs + chunkDurationMs);
      for await (const chunk of module.demuxAudio({ proxyUrl: request.proxyUrl, startUs: startMs * 1_000, endUs: endMs * 1_000 })) {
        if (cancelled.has(request.id)) break;
        decoder.decode(new EncodedAudioChunk(chunk));
        if (decoder.decodeQueueSize > 32) await decoder.flush();
      }
      await decoder.flush();
      await queuedAnalysis;
      workerScope.postMessage({ type: "progress", id: request.id, progress: Math.round(endMs / request.durationMs * 100) });
    }
    if (cancelled.has(request.id)) return;
    const lods = accumulator.toPayload();
    workerScope.postMessage({ type: "complete", id: request.id, lods }, lods.map((lod) => lod.values));
  } finally {
    decoder.close();
  }
}

const workerScope = self as unknown as {
  onmessage: ((event: MessageEvent<AnalyseRequest | { type: "cancel"; id: string }>) => void) | null;
  postMessage: (payload: unknown, transfer?: Transferable[]) => void;
};
const cancelled = new Set<string>();

workerScope.onmessage = (event) => {
  if (event.data.type === "cancel") { cancelled.add(event.data.id); return; }
  cancelled.delete(event.data.id);
  void analyse(event.data, cancelled).catch((error: unknown) => workerScope.postMessage({ type: "error", id: event.data.id, message: error instanceof Error ? error.message : "音軌分析失敗" }));
};
