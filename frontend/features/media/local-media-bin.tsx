"use client";

import { useEffect, useRef, useState } from "react";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import { listLocalMedia, pickVideoHandles, saveLocalMedia, type LocalMediaRecord } from "@/lib/local-media/local-media-library";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PART_SIZE = 16 * 1024 * 1024;

type UploadState = { progress: number; status: "local" | "uploading" | "ready" | "error"; message?: string };

async function uploadToProject(file: File, projectId: string, onProgress: (progress: number) => void) {
  const initiate = await authenticatedFetch(`${API_URL}/api/v1/media/multipart-upload/initiate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, filename: file.name, size_bytes: file.size, content_type: file.type || "video/mp4", media_type: file.type.startsWith("image/") ? "image" : file.type.startsWith("audio/") ? "audio" : "video" }),
  });
  const initiated = await initiate.json() as { asset_id?: string; upload_id?: string; part_size_bytes?: number; detail?: string };
  if (!initiate.ok || !initiated.asset_id || !initiated.upload_id) throw new Error(initiated.detail ?? "無法建立上傳工作");
  const partSize = initiated.part_size_bytes ?? PART_SIZE;
  const partCount = Math.max(1, Math.ceil(file.size / partSize));
  const parts: Array<{ part_number: number; etag: string }> = [];
  for (let index = 0; index < partCount; index += 1) {
    const partNumber = index + 1;
    const partUrlResponse = await authenticatedFetch(`${API_URL}/api/v1/media/multipart-upload/part-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_id: initiated.asset_id, upload_id: initiated.upload_id, part_number: partNumber }),
    });
    const partUrl = await partUrlResponse.json() as { upload_url?: string; detail?: string };
    if (!partUrlResponse.ok || !partUrl.upload_url) throw new Error(partUrl.detail ?? `無法取得第 ${partNumber} 段上傳網址`);
    const uploadResponse = await fetch(partUrl.upload_url, { method: "PUT", body: file.slice(index * partSize, Math.min(file.size, (index + 1) * partSize)) });
    const etag = uploadResponse.headers.get("etag");
    if (!uploadResponse.ok || !etag) throw new Error(`第 ${partNumber} 段上傳失敗`);
    parts.push({ part_number: partNumber, etag });
    onProgress(partNumber / partCount);
  }
  const complete = await authenticatedFetch(`${API_URL}/api/v1/media/multipart-upload/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_id: initiated.asset_id, upload_id: initiated.upload_id, parts }),
  });
  if (!complete.ok) {
    const body = await complete.json() as { detail?: string };
    throw new Error(body.detail ?? "無法完成素材上傳");
  }
}

export function LocalMediaBin({ projectId }: { projectId?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [records, setRecords] = useState<LocalMediaRecord[]>([]);
  const [uploads, setUploads] = useState<Record<string, UploadState>>({});
  const [dragging, setDragging] = useState(false);

  useEffect(() => { void listLocalMedia().then(setRecords).catch(() => setRecords([])); }, []);

  const addFiles = async (files: Array<{ file: File; handle?: FileSystemFileHandle }>) => {
    for (const { file, handle } of files) {
      const id = crypto.randomUUID();
      const record: LocalMediaRecord = { id, name: file.name, type: file.type, size: file.size, lastModified: file.lastModified, cacheKey: `local:${id}`, handle };
      await saveLocalMedia(record);
      setRecords((current) => [record, ...current]);
      setUploads((current) => ({ ...current, [id]: { progress: 0, status: projectId ? "uploading" : "local" } }));
      if (projectId) void uploadToProject(file, projectId, (progress) => setUploads((current) => ({ ...current, [id]: { progress, status: "uploading" } })))
        .then(() => setUploads((current) => ({ ...current, [id]: { progress: 1, status: "ready" } })))
        .catch((error: unknown) => setUploads((current) => ({ ...current, [id]: { progress: current[id]?.progress ?? 0, status: "error", message: error instanceof Error ? error.message : "上傳失敗" } })));
    }
  };

  const openPicker = async () => {
    const handles = await pickVideoHandles();
    if (!handles.length) { inputRef.current?.click(); return; }
    await addFiles(await Promise.all(handles.map(async (handle) => ({ handle, file: await handle.getFile() }))));
  };

  return (
    <section aria-labelledby="local-media-title" className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--lr-color-border)] px-4 py-3"><div><h2 id="local-media-title" className="text-sm font-semibold">本機素材</h2><p className="mt-1 text-xs text-[var(--lr-color-text-muted)]">先加入工作區，再於背景同步。</p></div><button type="button" onClick={() => void openPicker()} className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">選取影片</button></div>
      <input ref={inputRef} className="sr-only" type="file" multiple accept="video/*,image/*,audio/*" onChange={(event) => void addFiles(Array.from(event.target.files ?? []).map((file) => ({ file })))} />
      <div onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); void addFiles(Array.from(event.dataTransfer.files).map((file) => ({ file }))); }} className={`m-3 min-h-28 border border-dashed p-4 ${dragging ? "border-[var(--lr-color-primary)] bg-[var(--lr-color-primary-soft)]/40" : "border-[var(--lr-color-border-strong)]"}`}>
        {records.length === 0 ? <div className="grid min-h-20 place-items-center text-center text-xs text-[var(--lr-color-text-muted)]">拖入影片、圖片或音訊<br />素材會先保存在此瀏覽器</div> : <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{records.map((record) => { const upload = uploads[record.id]; return <li key={record.id} className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface-raised)] p-3"><div className="flex justify-between gap-3"><span className="truncate text-xs font-medium" title={record.name}>{record.name}</span><span className="shrink-0 font-mono text-[10px] text-[var(--lr-color-text-muted)]">{(record.size / 1048576).toFixed(1)} MB</span></div><div className="mt-2 h-1 bg-[var(--lr-color-border)]"><span className="block h-full bg-[var(--lr-color-secondary)]" style={{ width: `${Math.round((upload?.progress ?? 0) * 100)}%` }} /></div><p className={`mt-2 text-[10px] ${upload?.status === "error" ? "text-[var(--lr-color-error)]" : "text-[var(--lr-color-text-muted)]"}`}>{upload?.status === "uploading" ? `同步中 ${Math.round(upload.progress * 100)}%` : upload?.status === "ready" ? "已同步" : upload?.status === "error" ? upload.message : "本機可用"}</p></li>; })}</ul>}
      </div>
    </section>
  );
}
