"use client";

export interface LocalMediaRecord { id: string; name: string; type: string; size: number; lastModified: number; cacheKey: string; handle?: FileSystemFileHandle; }

const DATABASE = "ai-video-local-media"; const STORE = "handles";

function openDatabase(): Promise<IDBDatabase> { return new Promise((resolve, reject) => { const request = indexedDB.open(DATABASE, 1); request.onupgradeneeded = () => request.result.createObjectStore(STORE); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); }

export async function saveLocalMedia(record: LocalMediaRecord): Promise<void> { const db = await openDatabase(); return new Promise<void>((resolve, reject) => { const request = db.transaction(STORE, "readwrite").objectStore(STORE).put(record, record.id); request.onsuccess = () => resolve(); request.onerror = () => reject(request.error); }).finally(() => db.close()); }
export async function listLocalMedia(): Promise<LocalMediaRecord[]> { const db = await openDatabase(); return new Promise<LocalMediaRecord[]>((resolve, reject) => { const request = db.transaction(STORE, "readonly").objectStore(STORE).getAll(); request.onsuccess = () => resolve(request.result as LocalMediaRecord[]); request.onerror = () => reject(request.error); }).finally(() => db.close()); }

export async function pickVideoHandles(): Promise<FileSystemFileHandle[]> {
  const picker = (window as Window & { showOpenFilePicker?: (options: unknown) => Promise<FileSystemFileHandle[]> }).showOpenFilePicker;
  if (!picker) return [];
  return picker({ multiple: true, types: [{ description: "影片素材", accept: { "video/*": [".mp4", ".mov", ".webm", ".mkv"] } }] });
}

export async function handleFromDrop(item: DataTransferItem): Promise<FileSystemFileHandle | null> {
  const candidate = item as DataTransferItem & { getAsFileSystemHandle?: () => Promise<FileSystemHandle | null> };
  const handle = await candidate.getAsFileSystemHandle?.(); return handle?.kind === "file" ? handle as FileSystemFileHandle : null;
}
