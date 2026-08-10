import type { CloudDraftEditorState, CloudDraftTimeline } from "@/features/editor/timeline-store";

const DATABASE_NAME = "ai-video-editor-recovery";
const DATABASE_VERSION = 1;
const SNAPSHOTS = "snapshots";
const BLOBS = "blobs";

export interface CrashSnapshot {
  timelineId: string;
  savedAtMs: number;
  timeline: CloudDraftTimeline;
  editorState: CloudDraftEditorState;
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(SNAPSHOTS)) db.createObjectStore(SNAPSHOTS, { keyPath: "timelineId" });
      if (!db.objectStoreNames.contains(BLOBS)) db.createObjectStore(BLOBS, { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Unable to open recovery database"));
  });
}

export async function saveCrashSnapshot(snapshot: CrashSnapshot): Promise<void> {
  const db = await database();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(SNAPSHOTS, "readwrite");
    transaction.objectStore(SNAPSHOTS).put(snapshot);
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
  db.close();
}

export async function readCrashSnapshot(timelineId: string): Promise<CrashSnapshot | null> {
  const db = await database();
  const value = await new Promise<CrashSnapshot | undefined>((resolve, reject) => {
    const request = db.transaction(SNAPSHOTS, "readonly").objectStore(SNAPSHOTS).get(timelineId);
    request.onsuccess = () => resolve(request.result as CrashSnapshot | undefined); request.onerror = () => reject(request.error);
  });
  db.close();
  return value ?? null;
}

/** Store optional local proxy/recording fragments without ever duplicating originals in localStorage. */
export async function saveRecoveryBlob(key: string, blob: Blob, timelineId: string): Promise<void> {
  const db = await database();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(BLOBS, "readwrite");
    transaction.objectStore(BLOBS).put({ key, timelineId, blob, savedAtMs: Date.now() });
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
  db.close();
}

export async function readRecoveryBlob(key: string): Promise<Blob | null> {
  const db = await database();
  const value = await new Promise<{ blob?: Blob } | undefined>((resolve, reject) => {
    const request = db.transaction(BLOBS, "readonly").objectStore(BLOBS).get(key);
    request.onsuccess = () => resolve(request.result as { blob?: Blob } | undefined); request.onerror = () => reject(request.error);
  });
  db.close();
  return value?.blob ?? null;
}
