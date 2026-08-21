import type { WorkspaceLayoutDocument } from "@/types/workspace";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function loadWorkspaceLayout(projectId: string, userId: string): Promise<WorkspaceLayoutDocument | null> {
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/projects/${projectId}/workspace`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("無法讀取工作區偏好");
  return (await response.json()).layout as WorkspaceLayoutDocument;
}

export async function saveWorkspaceLayout(projectId: string, userId: string, layout: WorkspaceLayoutDocument): Promise<void> {
  const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/projects/${projectId}/workspace`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout }),
  });
  if (!response.ok) throw new Error("無法儲存工作區偏好");
}
