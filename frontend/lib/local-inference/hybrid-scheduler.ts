export type LocalInferenceTask = "asr" | "yolo" | "rough-cut" | "multimodal";
export type InferenceRoute = "local" | "remote";
export type SchedulerState = "idle" | "probing" | "benchmarking" | "local_ready" | "local_running" | "fallback_pending" | "remote_queued" | "failed";

export interface DeviceProfile {
  webGpu: boolean;
  webNn: boolean;
  hardwareConcurrency: number;
  deviceMemoryGb?: number;
}

export interface RoutingDecision {
  route: InferenceRoute;
  reason: string;
  state: SchedulerState;
}

export interface SchedulerSnapshot {
  state: SchedulerState;
  benchmarkMs?: number;
}

export interface HybridInferencePolicy {
  maxLocalLatencyMs: number;
  minDeviceMemoryGb: number;
  allowedLocalTasks: LocalInferenceTask[];
}

export const defaultHybridInferencePolicy: HybridInferencePolicy = {
  maxLocalLatencyMs: 1_500,
  minDeviceMemoryGb: 4,
  allowedLocalTasks: ["asr", "yolo", "rough-cut"],
};

export class HybridInferenceScheduler {
  private state: SchedulerState = "idle";
  private benchmarkMs?: number;

  public get snapshot(): SchedulerSnapshot {
    return { state: this.state, benchmarkMs: this.benchmarkMs };
  }

  public async probe(): Promise<DeviceProfile> {
    this.state = "probing";
    const nav = navigator as Navigator & { gpu?: unknown; ml?: unknown; deviceMemory?: number };
    return {
      webGpu: Boolean(nav.gpu),
      webNn: Boolean(nav.ml),
      hardwareConcurrency: navigator.hardwareConcurrency || 1,
      deviceMemoryGb: nav.deviceMemory,
    };
  }

  public async benchmark(run: () => Promise<unknown>, iterations = 2): Promise<number> {
    this.state = "benchmarking";
    await run(); // warm-up session/model compilation is deliberately excluded
    const starts = performance.now();
    for (let index = 0; index < iterations; index += 1) await run();
    this.benchmarkMs = (performance.now() - starts) / iterations;
    this.state = "local_ready";
    return this.benchmarkMs;
  }

  /** Avoid downloading/compiling a model when local execution is impossible. */
  public canBenchmark(
    task: LocalInferenceTask,
    profile: DeviceProfile,
    policy = defaultHybridInferencePolicy,
  ): boolean {
    if (!policy.allowedLocalTasks.includes(task) || task === "multimodal") return false;
    if (!profile.webGpu && !profile.webNn) return false;
    return (profile.deviceMemoryGb ?? 0) === 0 || (profile.deviceMemoryGb ?? 0) >= policy.minDeviceMemoryGb;
  }

  public decide(task: LocalInferenceTask, profile: DeviceProfile, policy = defaultHybridInferencePolicy): RoutingDecision {
    if (!policy.allowedLocalTasks.includes(task) || task === "multimodal") {
      this.state = "remote_queued";
      return { route: "remote", state: this.state, reason: "此任務需要後端的大型多模態模型。" };
    }
    if (!profile.webGpu && !profile.webNn) {
      this.state = "remote_queued";
      return { route: "remote", state: this.state, reason: "瀏覽器不支援 WebGPU/WebNN。" };
    }
    if ((profile.deviceMemoryGb ?? 0) > 0 && (profile.deviceMemoryGb ?? 0) < policy.minDeviceMemoryGb) {
      this.state = "remote_queued";
      return { route: "remote", state: this.state, reason: "可用裝置記憶體低於本地模型門檻。" };
    }
    if (this.benchmarkMs === undefined || this.benchmarkMs > policy.maxLocalLatencyMs) {
      this.state = "remote_queued";
      return { route: "remote", state: this.state, reason: "本地基準推理延遲超出門檻。" };
    }
    this.state = "local_ready";
    return { route: "local", state: this.state, reason: "本地 NPU/GPU 基準測試合格。" };
  }

  public localStarted(): void { this.state = "local_running"; }
  public localCompleted(executionMs: number): void {
    this.benchmarkMs = executionMs;
    this.state = "local_ready";
  }
  public fallback(): void { this.state = "fallback_pending"; }
  public remoteQueued(): void { this.state = "remote_queued"; }
  public failed(): void { this.state = "failed"; }
}
