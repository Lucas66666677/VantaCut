from uuid import UUID

from pydantic import BaseModel


class RoughCutRequest(BaseModel):
    user_id: UUID
    media_asset_id: UUID


class RoughCutQueuedResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str


class RoughCutResultResponse(BaseModel):
    analysis_id: UUID
    media_asset_id: UUID
    status: str
    clip_analysis: list[dict[str, object]]
    timeline_suggestions: list[dict[str, object]]
