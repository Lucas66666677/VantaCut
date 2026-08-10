/// <reference lib="webworker" />

export {};

type CacheRequest = { type: "cache"; id: string; file: File; key: string } | { type: "read"; id: string; key: string };
type SyncFileHandle = FileSystemFileHandle & { createSyncAccessHandle(): Promise<{ write(data: BufferSource, options?: { at?: number }): number; read(buffer: BufferSource, options?: { at?: number }): number; truncate(size: number): void; getSize(): number; close(): void }> };

async function cacheFile(file: File, key: string): Promise<number> {
  const root = await navigator.storage.getDirectory();
  const directory = await root.getDirectoryHandle("video-editor-cache", { create: true });
  const fileHandle = await directory.getFileHandle(key, { create: true }) as SyncFileHandle;
  const access = await fileHandle.createSyncAccessHandle();
  try {
    const blockSize = 4 * 1024 * 1024;
    for (let offset = 0; offset < file.size; offset += blockSize) {
      const bytes = new Uint8Array(await file.slice(offset, Math.min(file.size, offset + blockSize)).arrayBuffer());
      access.write(bytes, { at: offset });
    }
    access.truncate(file.size); return file.size;
  } finally { access.close(); }
}

async function readFile(key: string): Promise<ArrayBuffer> {
  const root = await navigator.storage.getDirectory(); const directory = await root.getDirectoryHandle("video-editor-cache");
  const fileHandle = await directory.getFileHandle(key) as SyncFileHandle; const access = await fileHandle.createSyncAccessHandle();
  try { const bytes = new Uint8Array(access.getSize()); access.read(bytes, { at: 0 }); return bytes.buffer; } finally { access.close(); }
}

self.onmessage = (event: MessageEvent<CacheRequest>) => {
  const request = event.data;
  if (request.type === "cache") void cacheFile(request.file, request.key).then((bytes) => self.postMessage({ type: "cached", id: request.id, key: request.key, bytes })).catch((error: unknown) => self.postMessage({ type: "error", id: request.id, message: error instanceof Error ? error.message : "OPFS cache failed" }));
  if (request.type === "read") void readFile(request.key).then((bytes) => self.postMessage({ type: "read", id: request.id, key: request.key, bytes }, [bytes])).catch((error: unknown) => self.postMessage({ type: "error", id: request.id, message: error instanceof Error ? error.message : "OPFS read failed" }));
};
