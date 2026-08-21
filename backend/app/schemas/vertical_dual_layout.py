from uuid import UUID

from pydantic import BaseModel, Field


class VerticalDualLayoutRequest(BaseModel):
    source_asset_id: UUID | None = None
    top_ratio: float = Field(default=.43, ge=.30, le=.60)
    max_samples: int = Field(default=48, ge=8, le=180)


class VerticalDualLayoutResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
