from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.entities import TrackType


class TimelineTrackClip(BaseModel):
    id: UUID | None = None
    source_asset_id: UUID
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    timeline_start: float = Field(default=0, ge=0)
    action: Literal["keep", "remove"] = "keep"
    z_index: int = Field(default=0, ge=0)
    audio_enabled: bool = True
    audio_effects: list[str] = Field(default_factory=list)

    @field_validator("source_end")
    @classmethod
    def source_end_must_follow_start(cls, value: float, info) -> float:
        if value <= info.data.get("source_start", 0):
            raise ValueError("source_end must be greater than source_start")
        return value


class TimelineTrack(BaseModel):
    type: TrackType
    z_index: int = Field(default=0, ge=0)
    clips: list[TimelineTrackClip] = Field(default_factory=list)


class MultiTrackTimelineJSON(BaseModel):
    version: int = 2
    tracks: list[TimelineTrack]
