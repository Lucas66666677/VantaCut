from uuid import UUID

from pydantic import BaseModel, Field


class AudioSyncRequest(BaseModel):
    video_asset_id: UUID
    external_audio_asset_id: UUID
    max_offset_seconds: float = Field(default=120.0, gt=1, le=1800)


class AudioSyncTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str


class AudioSyncStatusResponse(BaseModel):
    status: str
    offset_seconds: float | None = None
    confidence: float | None = None
    audio_clip: dict[str, object] | None = None
    error: str | None = None
