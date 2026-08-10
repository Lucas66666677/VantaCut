"use client";

import { useCallback, useRef, useState } from "react";

import {
  HybridInferenceScheduler,
  type RoutingDecision,
  type InferenceRoute,
  type LocalInferenceTask,
  type SchedulerSnapshot,
} from "./hybrid-scheduler";

export type { LocalInferenceTask } from "./hybrid-scheduler";

type OrtModule = typeof import("onnxruntime-web");
type ExecutionProvider = "webnn" | "webgpu" | "wasm";
type TensorType = "float32" | "int32" | "int64" | "uint8" | "bool";

export interface LocalModelSpec {
  id: string;
  task: LocalInferenceTask;
  url: string;
  inputName: string;
  outputNames?: string[];
}

export interface TensorInput {
  data: Float32Array | Int32Array | BigInt64Array | Uint8Array;
  dims: readonly number[];
  type?: TensorType;
}

export interface RemoteInferenceRequest {
  task: LocalInferenceTask;
  modelId: string;
  reason: string;
  payload: unknown;
}

export interface LocalInferenceResult<T = Record<string, unknown>> {
  route: InferenceRoute;
  output: T;
  provider?: ExecutionProvider;
  elapsedMs?: number;
}

type Session = {
  run(feeds: Record<string, unknown>): Promise<Record<string, unknown>>;
};

type BrowserCapabilities = Navigator & {
  gpu?: unknown;
  ml?: unknown;
  deviceMemory?: number;
};

const sessionCache = new Map<string, { session: Session; provider: ExecutionProvider }>();

/**
 * Runs small ONNX models on-device and yields to FastAPI/Celery when the local
 * benchmark or runtime fails. The caller owns preprocessing/postprocessing and
 * the remote callback, so it can map ASR and rough-cut requests to existing APIs.
 */
export function useLocalInference() {
  const schedulerRef = useRef(new HybridInferenceScheduler());
  const [status, setStatus] = useState<SchedulerSnapshot>(() => schedulerRef.current.snapshot);
  const [error, setError] = useState<string | null>(null);

  const syncStatus = useCallback(() => setStatus(schedulerRef.current.snapshot), []);

  const loadOrt = useCallback(async (provider: ExecutionProvider): Promise<OrtModule> => {
    // The WebGPU bundle excludes the slower WebGL code path. WebNN and WASM are
    // loaded from the regular bundle because they are selected at session creation.
    const ort = provider === "webgpu"
      ? await import("onnxruntime-web/webgpu")
      : await import("onnxruntime-web");

    ort.env.wasm.wasmPaths = "/ort/";
    ort.env.wasm.numThreads = Math.max(1, Math.min(4, navigator.hardwareConcurrency || 1));
    ort.env.wasm.simd = true;
    return ort as OrtModule;
  }, []);

  const createSession = useCallback(async (spec: LocalModelSpec) => {
    const capabilities = navigator as BrowserCapabilities;
    const candidates: Array<{ provider: ExecutionProvider; webnnDeviceType?: "npu" | "gpu" }> = [
      // A browser that exposes WebNN may still reject NPU. Try it first, then
      // the WebNN GPU implementation before using direct WebGPU.
      ...(capabilities.ml
        ? [
            { provider: "webnn" as const, webnnDeviceType: "npu" as const },
            { provider: "webnn" as const, webnnDeviceType: "gpu" as const },
          ]
        : []),
      ...(capabilities.gpu ? [{ provider: "webgpu" as const }] : []),
      { provider: "wasm" as const },
    ];
    let lastError: unknown;

    for (const candidate of candidates) {
      const { provider } = candidate;
      const cacheKey = `${spec.id}:${provider}:${candidate.webnnDeviceType ?? ""}`;
      const cached = sessionCache.get(cacheKey);
      if (cached) return cached;

      try {
        const ort = await loadOrt(provider);
        const executionProviders = provider === "webnn"
          ? [{ name: "webnn", deviceType: candidate.webnnDeviceType }]
          : [provider];
        const session = await ort.InferenceSession.create(spec.url, {
          executionProviders,
          graphOptimizationLevel: "all",
        } as Parameters<typeof ort.InferenceSession.create>[1]);
        const result = { session: session as unknown as Session, provider };
        sessionCache.set(cacheKey, result);
        return result;
      } catch (candidateError) {
        lastError = candidateError;
      }
    }

    throw lastError ?? new Error("No ONNX Runtime Web execution provider is available.");
  }, [loadOrt]);

  const buildFeeds = useCallback(async (input: TensorInput, spec: LocalModelSpec) => {
    // Tensor construction is kept here so model-specific feature extraction
    // (Whisper log-Mel or YOLO letterboxing) stays isolated in the UI feature.
    const ort = await loadOrt("wasm");
    return {
      [spec.inputName]: new ort.Tensor(input.type ?? "float32", input.data, [...input.dims]),
    } as Record<string, unknown>;
  }, [loadOrt]);

  const prepare = useCallback(async (spec: LocalModelSpec, benchmarkInput: TensorInput) => {
    setError(null);
    const deviceProfile = await schedulerRef.current.probe();
    syncStatus();

    if (!schedulerRef.current.canBenchmark(spec.task, deviceProfile)) {
      const decision = schedulerRef.current.decide(spec.task, deviceProfile);
      syncStatus();
      return { decision };
    }

    const sessionInfo = await createSession(spec);
    const feeds = await buildFeeds(benchmarkInput, spec);
    syncStatus();

    const elapsedMs = await schedulerRef.current.benchmark(() => sessionInfo.session.run(feeds));
    const decision = schedulerRef.current.decide(spec.task, deviceProfile);
    syncStatus();
    return { ...sessionInfo, elapsedMs, decision };
  }, [buildFeeds, createSession, syncStatus]);

  const runOrFallback = useCallback(async <T = Record<string, unknown>>({
    spec,
    input,
    payload,
    remoteFallback,
  }: {
    spec: LocalModelSpec;
    input: TensorInput;
    payload: unknown;
    remoteFallback: (request: RemoteInferenceRequest) => Promise<T>;
  }): Promise<LocalInferenceResult<T>> => {
    let sessionInfo: {
      session?: Session;
      provider?: ExecutionProvider;
      elapsedMs?: number;
      decision: RoutingDecision;
    } | undefined;
    try {
      // Full multimodal understanding deliberately bypasses browser models.
      if (spec.task !== "multimodal") sessionInfo = await prepare(spec, input);

      if (!sessionInfo || !sessionInfo.session || !sessionInfo.provider || sessionInfo.decision.route !== "local") {
        const reason = spec.task === "multimodal"
          ? "This task requires the server multimodal provider."
          : "Local benchmark did not meet the device performance policy.";
        schedulerRef.current.fallback();
        syncStatus();
        const output = await remoteFallback({ task: spec.task, modelId: spec.id, reason, payload });
        schedulerRef.current.remoteQueued();
        syncStatus();
        return { route: "remote", output };
      }

      schedulerRef.current.localStarted();
      syncStatus();
      const startedAt = performance.now();
      const output = await sessionInfo.session.run(await buildFeeds(input, spec));
      const elapsedMs = performance.now() - startedAt;
      schedulerRef.current.localCompleted(elapsedMs);
      syncStatus();
      return { route: "local", output: output as T, provider: sessionInfo.provider, elapsedMs };
    } catch (runError) {
      const reason = runError instanceof Error ? runError.message : "Local inference failed.";
      setError(reason);
      schedulerRef.current.fallback();
      syncStatus();
      try {
        const output = await remoteFallback({ task: spec.task, modelId: spec.id, reason, payload });
        schedulerRef.current.remoteQueued();
        syncStatus();
        return { route: "remote", output };
      } catch (remoteError) {
        const message = remoteError instanceof Error ? remoteError.message : "Remote inference could not be queued.";
        setError(message);
        schedulerRef.current.failed();
        syncStatus();
        throw remoteError;
      }
    }
  }, [buildFeeds, prepare, syncStatus]);

  return { status, error, runOrFallback };
}
