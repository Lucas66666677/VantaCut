from uuid import UUID

from pydantic import BaseModel, Field


class SmartAudioRemixRequest(BaseModel):
    user_id: UUID
    bgm_asset_id: UUID | None = None
    target_duration_seconds: float | None = Field(default=None, ge=1, le=4 * 60 * 60)
    mix_level: float = Field(default=.16, ge=0, le=.9)


class SmartAudioRemixResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class SmartAudioRemixStatusResponse(BaseModel):
    status: str
    target_duration_seconds: float | None = None
    bpm: float | None = None
    sections: list[dict[str, object]] = []
    error: str | None = None
