/// <reference lib="webworker" />

export {};

interface Initiate { asset_id: string; upload_id: string; part_size_bytes: number; }
interface UploadRequest { type: "upload"; localId: string; projectId: string; file: File; apiUrl: string; }

async function json<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await response.json() as T & { detail?: string }; if (!response.ok) throw new Error(payload.detail ?? "Background upload failed"); return payload;
}

async function upload(request: UploadRequest) {
  // Yield once so adding local media never competes with the user's first edit.
  await new Promise((resolve) => setTimeout(resolve, 1_000));
  const initiated = await json<Initiate>(`${request.apiUrl}/api/v1/media/multipart-upload/initiate`, { project_id: request.projectId, filename: request.file.name, size_bytes: request.file.size, content_type: request.file.type || "video/mp4", media_type: request.file.type.startsWith("image/") ? "image" : "video" });
  const parts: Array<{ part_number: number; etag: string }> = []; const total = Math.max(1, Math.ceil(request.file.size / initiated.part_size_bytes));
  for (let index = 0; index < total; index += 1) {
    const partNumber = index + 1;
    const { upload_url } = await json<{ upload_url: string }>(`${request.apiUrl}/api/v1/media/multipart-upload/part-url`, { asset_id: initiated.asset_id, upload_id: initiated.upload_id, part_number: partNumber });
    const body = await request.file.slice(index * initiated.part_size_bytes, Math.min(request.file.size, (index + 1) * initiated.part_size_bytes)).arrayBuffer();
    const response = await fetch(upload_url, { method: "PUT", body }); if (!response.ok) throw new Error(`Part ${partNumber} upload failed`);
    const etag = response.headers.get("etag"); if (!etag) throw new Error("Storage CORS must expose the ETag header for resumable uploads");
    parts.push({ part_number: partNumber, etag }); self.postMessage({ type: "progress", localId: request.localId, progress: partNumber / total });
  }
  const asset = await json<{ id: string; status: string }>(`${request.apiUrl}/api/v1/media/multipart-upload/complete`, { asset_id: initiated.asset_id, upload_id: initiated.upload_id, parts });
  self.postMessage({ type: "completed", localId: request.localId, assetId: asset.id, status: asset.status });
}

self.onmessage = (event: MessageEvent<UploadRequest>) => { if (event.data.type === "upload") void upload(event.data).catch((error: unknown) => self.postMessage({ type: "error", localId: event.data.localId, message: error instanceof Error ? error.message : "Background upload failed" })); };
