from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LongToShortsRequest(BaseModel):
    user_id: UUID
    source_media_asset_id: UUID
    count: int = Field(default=3, ge=3, le=3)
    min_duration_seconds: float = Field(default=45, ge=30, le=60)
    max_duration_seconds: float = Field(default=60, ge=45, le=75)


class LongToShortsResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class LongToShortsBatchRequest(BaseModel):
    user_id: UUID
    resolution: Literal["720p", "1080p"] = "1080p"


class LongToShortsStatusResponse(BaseModel):
    status: str
    shorts: list[dict[str, object]] = []
    download_url: str | None = None
    source_preview_url: str | None = None
    error: str | None = None
