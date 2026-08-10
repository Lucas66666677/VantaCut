import type { DockLayoutSnapshot } from "@/features/workspace/dock-layout";

const DB_NAME = "ai-video-editor";
const STORE = "dock-layouts";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function loadDockLayout(key: string): Promise<DockLayoutSnapshot | null> {
  const db = await openDatabase();
  return new Promise<DockLayoutSnapshot | null>((resolve, reject) => {
    const request = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
    request.onsuccess = () => resolve((request.result as DockLayoutSnapshot | undefined) ?? null);
    request.onerror = () => reject(request.error);
  }).finally(() => db.close());
}

export async function saveDockLayout(key: string, snapshot: DockLayoutSnapshot): Promise<void> {
  const db = await openDatabase();
  return new Promise<void>((resolve, reject) => {
    const request = db.transaction(STORE, "readwrite").objectStore(STORE).put(snapshot, key);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  }).finally(() => db.close());
}
