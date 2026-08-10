from uuid import UUID

from pydantic import BaseModel, Field


class AutoSFXRequest(BaseModel):
    user_id: UUID
    pop_asset_id: UUID | None = None
    whoosh_asset_id: UUID | None = None
    impact_asset_id: UUID | None = None
    bgm_asset_id: UUID | None = None
    bgm_volume: float = Field(default=.16, ge=0, le=.9)
    ducking_enabled: bool = True


class AutoSFXResponse(BaseModel):
    timeline_id: UUID
    status: str
    event_count: int
    track: dict[str, object]
