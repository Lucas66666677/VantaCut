from uuid import UUID

from pydantic import BaseModel, Field


class BeatSyncRequest(BaseModel):
    bgm_asset_id: UUID
    source_asset_id: UUID
    max_cut_suggestions: int = Field(default=24, ge=1, le=100)
    detect_drops: bool = True


class BeatSyncResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class BeatSyncMontageRequest(BaseModel):
    bgm_asset_id: UUID
    media_asset_ids: list[UUID] = Field(min_length=10, max_length=30)
    aspect_ratio: str = Field(default="9:16", pattern="^(9:16|16:9)$")
    resolution: str = Field(default="1080p", pattern="^(720p|1080p)$")
    auto_render: bool = False


class BeatSyncMontageResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
