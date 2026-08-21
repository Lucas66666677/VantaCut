from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OneClickTemplateResponse(BaseModel):
    id: str
    name: str
    aspect_ratio: Literal["16:9", "9:16"]
    bgm: dict[str, object]
    slot_count: int
    total_beats: int


class OneClickGenerateRequest(BaseModel):
    template_id: str = Field(min_length=1, max_length=100)
    media_asset_ids: list[UUID] = Field(min_length=1, max_length=80)
    bgm_asset_id: UUID | None = None
    resolution: Literal["720p", "1080p"] = "1080p"
    auto_render: bool = True


class OneClickGenerateResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
