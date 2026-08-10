from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MemeGifRequest(BaseModel):
    user_id: UUID
    source_asset_id: UUID | None = None
    provider: Literal["auto", "tenor", "giphy"] = "auto"
    insertion_mode: Literal["overlay", "cutaway"] = "overlay"
    max_events: int = Field(default=4, ge=1, le=8)
    bgm_asset_id: UUID | None = None
    comedic_sfx_asset_id: UUID | None = None


class MemeGifResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class MemeGifStatusResponse(BaseModel):
    status: str
    events: list[dict[str, object]] = []
    error: str | None = None
