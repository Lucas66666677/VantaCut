import type { ClientRenderRequest } from "@/types/client-render";

export type RenderRoute = "client" | "cloud";

export interface RenderRoutingDecision {
  route: RenderRoute;
  reason: string;
  memoryBudgetBytes: number;
  estimatedPeakBytes: number;
}

const MB = 1024 * 1024;

function deviceMemoryBytes(): number {
  if (typeof navigator === "undefined") return 1_536 * MB;
  const memoryGb = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  // Browsers may omit this privacy-sensitive hint. Use a deliberately small default budget.
  return (memoryGb && memoryGb > 0 ? memoryGb * 1024 : 1_536) * MB;
}

function mainClips(request: ClientRenderRequest) {
  const tracks = request.timeline.tracks;
  if (tracks) return tracks.find((track) => track.type === "main_video")?.clips ?? [];
  return request.timeline.segments ?? [];
}

export function decideRenderRoute(request: ClientRenderRequest): RenderRoutingDecision {
  const memoryBudgetBytes = Math.floor(deviceMemoryBytes() * 0.18);
  // MEMFS holds the input, FFmpeg working buffers, and an output copy. Chunking limits temporary
  // output, but cannot make an oversized source File disappear from the wasm filesystem.
  const estimatedPeakBytes = Math.ceil(request.source.file.size * 2.35 + request.source.file.size * 0.15);
  const effects = request.effects ?? {};
  const bRollClips = request.timeline.tracks?.filter((track) => track.type === "b_roll").flatMap((track) => track.clips) ?? [];
  const sourceIds = new Set((request.bRollSources ?? []).map((source) => source.id));
  const missingBRoll = bRollClips.some((clip) => !clip.source_asset_id || !sourceIds.has(clip.source_asset_id));

  if (typeof window === "undefined" || !window.Worker || !window.WebAssembly) return { route: "cloud", reason: "此瀏覽器不支援 Web Worker 或 WebAssembly。", memoryBudgetBytes, estimatedPeakBytes };
  if (request.resolution === "4k") return { route: "cloud", reason: "4K 導出固定使用雲端渲染。", memoryBudgetBytes, estimatedPeakBytes };
  if (request.estimatedDurationSeconds > 180) return { route: "cloud", reason: "影片長度超過 3 分鐘。", memoryBudgetBytes, estimatedPeakBytes };
  if (effects.hasAiGeneration || effects.hasDepthOr3d || effects.hasOpticalFlow || effects.hasHeavyColorPipeline) return { route: "cloud", reason: "時間軸含有需要 GPU Worker 的重型 AI／3D 特效。", memoryBudgetBytes, estimatedPeakBytes };
  if (missingBRoll) return { route: "cloud", reason: "B-Roll 沒有可用的本機檔案。", memoryBudgetBytes, estimatedPeakBytes };
  if (estimatedPeakBytes > memoryBudgetBytes) return { route: "cloud", reason: "來源檔案超出保守的瀏覽器記憶體預算。", memoryBudgetBytes, estimatedPeakBytes };
  if (mainClips(request).filter((clip) => clip.action === "keep").length === 0) return { route: "cloud", reason: "時間軸沒有可輸出的保留片段。", memoryBudgetBytes, estimatedPeakBytes };
  return { route: "client", reason: "解析度、時長、效果與記憶體預算符合本機導出條件。", memoryBudgetBytes, estimatedPeakBytes };
}
