import type { FrameAnnotation, ReviewComment } from "@/types/review";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function createReviewComment(timelineId: string, _userId: string, frameNumber: number, frameRate: number, body: string, annotation: FrameAnnotation): Promise<ReviewComment> {
  const response = await authenticatedFetch(`${apiBase}/api/v1/timelines/${timelineId}/review/comments`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ frame_number: frameNumber, frame_rate: frameRate, body, annotation }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<ReviewComment>;
}

export async function updateReviewCommentStatus(timelineId: string, commentId: string, _userId: string, status: "open" | "resolved"): Promise<ReviewComment> {
  const response = await authenticatedFetch(`${apiBase}/api/v1/timelines/${timelineId}/review/comments/${commentId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<ReviewComment>;
}
