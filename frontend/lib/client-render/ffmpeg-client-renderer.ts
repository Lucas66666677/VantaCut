import type { ClientRenderProgress, ClientRenderRequest } from "@/types/client-render";

type WorkerMessage =
  | (ClientRenderProgress & { type: "progress" })
  | { type: "completed"; jobId: string; bytes: ArrayBuffer }
  | { type: "error"; jobId: string; message: string };

export class ClientTimelineRenderer {
  private readonly worker = new Worker(new URL("./ffmpeg-render.worker.ts", import.meta.url), { type: "module" });

  async render(request: ClientRenderRequest, onProgress: (progress: ClientRenderProgress) => void): Promise<Blob> {
    const jobId = crypto.randomUUID();
    const sources = [request.source, ...(request.bRollSources ?? [])];
    const useSharedMemory = typeof SharedArrayBuffer !== "undefined" && crossOriginIsolated;
    const inputBuffers = await Promise.all(sources.map(async (source, index) => {
      const bytes = await source.file.arrayBuffer();
      const common = { id: source.id, name: `${index}-${source.file.name.replace(/[^a-zA-Z0-9._-]/g, "_")}`, byteLength: bytes.byteLength };
      if (!useSharedMemory) return { ...common, bytes };
      const sharedBytes = new SharedArrayBuffer(bytes.byteLength); new Uint8Array(sharedBytes).set(new Uint8Array(bytes));
      return { ...common, sharedBytes };
    }));
    return new Promise<Blob>((resolve, reject) => {
      const timeout = window.setTimeout(() => fail(new Error("Browser render timed out; queued for cloud rendering.")), 20 * 60 * 1000);
      const cleanup = () => { window.clearTimeout(timeout); this.worker.removeEventListener("message", listener); };
      const fail = (error: Error) => { cleanup(); reject(error); };
      const listener = (event: MessageEvent<WorkerMessage>) => {
        const data = event.data;
        if (data.type === "progress") onProgress(data);
        else if (data.type === "error" && data.jobId === jobId) fail(new Error(data.message));
        else if (data.type === "completed" && data.jobId === jobId) { cleanup(); resolve(new Blob([data.bytes], { type: "video/mp4" })); }
      };
      this.worker.addEventListener("message", listener);
      const transferables = inputBuffers.flatMap((input) => "bytes" in input ? [input.bytes] : []);
      this.worker.postMessage({ type: "render", jobId, request: { timeline: request.timeline, resolution: request.resolution, aspectRatio: request.aspectRatio, estimatedDurationSeconds: request.estimatedDurationSeconds, effects: request.effects }, inputs: inputBuffers }, transferables);
    });
  }

  dispose() { this.worker.postMessage({ type: "dispose" }); this.worker.terminate(); }
}
