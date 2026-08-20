import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import type { VirtualCameraKeyframe } from "@/types/spatial";

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function requestVirtualCameraRender(mediaAssetId: string, userId: string, cameraPath: VirtualCameraKeyframe[], settings: { fps: number; width: number; height: number }) {
  const response = await authenticatedFetch(`${apiBase}/api/v1/media/${mediaAssetId}/spatial-scene/render`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ camera_path: cameraPath, ...settings }) });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<{ task_id: string; status: string }>;
}
