/// <reference lib="webworker" />

import type { TimelineClipInput } from "@/types/timeline";
import type { ClientRenderProgress, ClientRenderRequest } from "@/types/client-render";

type WorkerInput = { id: string; name: string; byteLength: number; bytes?: ArrayBuffer; sharedBytes?: SharedArrayBuffer };
type RenderMessage = {
  type: "render";
  jobId: string;
  request: Omit<ClientRenderRequest, "source" | "bRollSources">;
  inputs: WorkerInput[];
};
type IncomingMessage = RenderMessage | { type: "dispose" };

let ffmpegInstance: InstanceType<typeof import("@ffmpeg/ffmpeg").FFmpeg> | null = null;
let runtime: "single-thread" | "multi-thread" = "single-thread";
let activeJob: { id: string; chunkIndex: number; chunkCount: number; chunkDurationSeconds: number; renderOffsetSeconds: number; estimatedDurationSeconds: number } | null = null;

function emit(message: ClientRenderProgress & { type: "progress" }) {
  self.postMessage(message);
}

async function threadedCoreAvailable(base: string): Promise<boolean> {
  if (!self.crossOriginIsolated || typeof SharedArrayBuffer === "undefined") return false;
  try { return (await fetch(`${base}/ffmpeg-core-mt/ffmpeg-core.js`, { method: "HEAD" })).ok; } catch { return false; }
}

async function ffmpeg() {
  if (ffmpegInstance) return ffmpegInstance;
  const { FFmpeg } = await import("@ffmpeg/ffmpeg");
  const instance = new FFmpeg();
  instance.on("progress", ({ progress }) => {
    if (!activeJob) return;
    const completed = activeJob.chunkIndex + Math.max(0, Math.min(1, progress));
    emit({ type: "progress", phase: "rendering_chunk", progress: completed / activeJob.chunkCount, chunkIndex: activeJob.chunkIndex + 1, chunkCount: activeJob.chunkCount, renderTimeSeconds: activeJob.estimatedDurationSeconds * completed / activeJob.chunkCount, runtime });
  });
  instance.on("log", ({ message }) => {
    if (!activeJob) return;
    const matched = message.match(/time=(\d+):(\d+):(\d+(?:\.\d+)?)/); if (!matched) return;
    const seconds = Number(matched[1]) * 3600 + Number(matched[2]) * 60 + Number(matched[3]);
    emit({ type: "progress", phase: "rendering_chunk", progress: Math.min(.99, (activeJob.chunkIndex + seconds / Math.max(.01, activeJob.chunkDurationSeconds)) / activeJob.chunkCount), chunkIndex: activeJob.chunkIndex + 1, chunkCount: activeJob.chunkCount, renderTimeSeconds: activeJob.renderOffsetSeconds + seconds, runtime });
  });
  const base = self.location.origin;
  if (await threadedCoreAvailable(base)) {
    try {
      await instance.load({ coreURL: `${base}/ffmpeg-core-mt/ffmpeg-core.js`, wasmURL: `${base}/ffmpeg-core-mt/ffmpeg-core.wasm`, workerURL: `${base}/ffmpeg-core-mt/ffmpeg-core.worker.js` }); runtime = "multi-thread";
    } catch { await instance.load({ coreURL: `${base}/ffmpeg-core/ffmpeg-core.js`, wasmURL: `${base}/ffmpeg-core/ffmpeg-core.wasm` }); runtime = "single-thread"; }
  } else await instance.load({ coreURL: `${base}/ffmpeg-core/ffmpeg-core.js`, wasmURL: `${base}/ffmpeg-core/ffmpeg-core.wasm` });
  ffmpegInstance = instance;
  return instance;
}

function dimensions(resolution: ClientRenderRequest["resolution"], aspectRatio: ClientRenderRequest["aspectRatio"]) {
  const key = `${resolution}:${aspectRatio}`;
  const values: Record<string, [number, number]> = { "720p:16:9": [1280, 720], "720p:9:16": [720, 1280], "1080p:16:9": [1920, 1080], "1080p:9:16": [1080, 1920] };
  const result = values[key];
  if (!result) throw new Error("Client rendering supports 720p/1080p only.");
  return result;
}

function mainClips(timeline: RenderMessage["request"]["timeline"]): TimelineClipInput[] {
  return timeline.tracks?.find((track) => track.type === "main_video")?.clips ?? timeline.segments ?? [];
}

function bRollClips(timeline: RenderMessage["request"]["timeline"]): TimelineClipInput[] {
  return timeline.tracks?.filter((track) => track.type === "b_roll").flatMap((track) => track.clips) ?? [];
}

function splitIntoChunks(clips: TimelineClipInput[], maxSeconds = 30): TimelineClipInput[][] {
  const chunks: TimelineClipInput[][] = []; let current: TimelineClipInput[] = []; let duration = 0;
  for (const clip of clips.filter((item) => item.action === "keep")) {
    const clipDuration = clip.source_end - clip.source_start;
    if (current.length && duration + clipDuration > maxSeconds) { chunks.push(current); current = []; duration = 0; }
    current.push(clip); duration += clipDuration;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

function buildFilter(clips: TimelineClipInput[], request: RenderMessage["request"], inputIndexes: Map<string, number>, includeBRoll: boolean) {
  const kept = clips.filter((clip) => clip.action === "keep");
  if (!kept.length) throw new Error("Timeline has no keep clips.");
  const filters: string[] = []; const concatInputs: string[] = [];
  kept.forEach((clip, index) => {
    filters.push(`[0:v]trim=start=${clip.source_start}:end=${clip.source_end},setpts=PTS-STARTPTS[v${index}]`);
    filters.push(`[0:a]atrim=start=${clip.source_start}:end=${clip.source_end},asetpts=PTS-STARTPTS[a${index}]`);
    concatInputs.push(`[v${index}][a${index}]`);
  });
  filters.push(`${concatInputs.join("")}concat=n=${kept.length}:v=1:a=1[basev][outa]`);
  const [width, height] = dimensions(request.resolution, request.aspectRatio);
  filters.push(`[basev]scale=w=${width}:h=${height}:force_original_aspect_ratio=decrease,pad=w=${width}:h=${height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black[scaledv]`);
  let current = "scaledv";
  if (includeBRoll) {
    bRollClips(request.timeline).filter((clip) => clip.action === "keep").forEach((clip, index) => {
      const inputIndex = clip.source_asset_id ? inputIndexes.get(clip.source_asset_id) : undefined;
      if (inputIndex === undefined) throw new Error("Missing local B-Roll file.");
      const label = `broll${index}`; const next = `overlay${index}`;
      filters.push(`[${inputIndex}:v]trim=start=${clip.source_start}:end=${clip.source_end},setpts=PTS-STARTPTS+${clip.timeline_start ?? 0}/TB,scale=w=${width}:h=${height}:force_original_aspect_ratio=decrease,pad=w=${width}:h=${height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black[${label}]`);
      filters.push(`[${current}][${label}]overlay=x=0:y=0:eof_action=pass:shortest=0[${next}]`); current = next;
    });
  }
  filters.push(`[${current}]null[outv]`);
  return filters.join(";");
}

async function removeFile(name: string) {
  try { await (await ffmpeg()).deleteFile(name); } catch { /* File was never created or was already reclaimed. */ }
}

async function render(message: RenderMessage) {
  const engine = await ffmpeg();
  emit({ type: "progress", phase: "loading", progress: 0, runtime });
  const source = message.inputs[0];
  if (!source) throw new Error("Client render input is missing.");
  const inputIndexes = new Map<string, number>();
  for (const [index, input] of message.inputs.entries()) {
    const inputBytes = input.sharedBytes ? new Uint8Array(input.sharedBytes, 0, input.byteLength) : new Uint8Array(input.bytes!);
    await engine.writeFile(input.name, inputBytes);
    inputIndexes.set(input.id, index);
  }
  const sourceName = source.name;
  const clips = mainClips(message.request.timeline).filter((clip) => clip.action === "keep");
  const hasBRoll = bRollClips(message.request.timeline).length > 0;
  // B-Roll overlays span final-timeline time, so render them as one graph. Simple main-track
  // timelines are split into <=30s chunks, releasing each temporary file after concat.
  const chunks = hasBRoll ? [clips] : splitIntoChunks(clips);
  const outputNames: string[] = [];
  try {
    for (const [index, chunk] of chunks.entries()) {
      const chunkDurationSeconds = chunk.reduce((total, clip) => total + clip.source_end - clip.source_start, 0);
      const renderOffsetSeconds = chunks.slice(0, index).flat().reduce((total, clip) => total + clip.source_end - clip.source_start, 0);
      activeJob = { id: message.jobId, chunkIndex: index, chunkCount: chunks.length, chunkDurationSeconds, renderOffsetSeconds, estimatedDurationSeconds: message.request.estimatedDurationSeconds };
      const outputName = `chunk-${index}.mp4`; outputNames.push(outputName);
      const filter = buildFilter(chunk, message.request, inputIndexes, hasBRoll);
      await engine.exec(["-i", sourceName, ...message.inputs.slice(1).flatMap((input) => ["-i", input.name]), "-filter_complex", filter, "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", outputName]);
    }
    const finalName = "export.mp4";
    if (outputNames.length === 1) {
      const data = await engine.readFile(outputNames[0]);
      const bytes = data instanceof Uint8Array ? data.slice() : new TextEncoder().encode(data);
      self.postMessage({ type: "completed", jobId: message.jobId, bytes: bytes.buffer }, [bytes.buffer]);
    } else {
      emit({ type: "progress", phase: "concatenating", progress: .96, renderTimeSeconds: message.request.estimatedDurationSeconds, runtime });
      await engine.writeFile("chunks.txt", new TextEncoder().encode(outputNames.map((name) => `file '${name}'`).join("\n")));
      await engine.exec(["-f", "concat", "-safe", "0", "-i", "chunks.txt", "-c", "copy", "-movflags", "+faststart", finalName]);
      const data = await engine.readFile(finalName);
      const bytes = data instanceof Uint8Array ? data.slice() : new TextEncoder().encode(data);
      self.postMessage({ type: "completed", jobId: message.jobId, bytes: bytes.buffer }, [bytes.buffer]);
      await removeFile("chunks.txt"); await removeFile(finalName);
    }
    emit({ type: "progress", phase: "completed", progress: 1, renderTimeSeconds: message.request.estimatedDurationSeconds, runtime });
  } finally {
    activeJob = null;
    await Promise.all([...message.inputs.map((input) => removeFile(input.name)), ...outputNames.map(removeFile)]);
  }
}

self.onmessage = (event: MessageEvent<IncomingMessage>) => {
  const request = event.data;
  if (request.type === "dispose") { ffmpegInstance?.terminate(); ffmpegInstance = null; self.close(); return; }
  render(request).catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "Browser rendering failed.";
    self.postMessage({ type: "error", jobId: request.jobId, message });
  });
};
