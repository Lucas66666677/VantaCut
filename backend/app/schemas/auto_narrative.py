from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


NarrativeTone = Literal["funny_vlogger", "emotional_vlogger"]


class AutoNarrativeRequest(BaseModel):
    user_id: UUID
    media_asset_ids: list[UUID] = Field(min_length=5, max_length=10)
    bgm_asset_id: UUID | None = None
    tone: NarrativeTone = "funny_vlogger"
    language: str = Field(default="zh", min_length=2, max_length=20)
    target_duration_seconds: int = Field(default=30, ge=20, le=45)
    resolution: Literal["720p", "1080p"] = "1080p"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    auto_render: bool = True

    @field_validator("media_asset_ids")
    @classmethod
    def unique_assets(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("media_asset_ids must not contain duplicates")
        return value


class AutoNarrativeResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: Literal["queued"]


class VisualUnderstanding(BaseModel):
    asset_id: str
    summary: str = Field(min_length=1, max_length=360)
    moments: list[str] = Field(min_length=1, max_length=5)
    best_source_start: float = Field(ge=0)
    best_source_end: float = Field(gt=0)
    mood: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)


class NarrativeBeat(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    asset_id: str
    narration: str = Field(min_length=1, max_length=280)
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    target_duration_seconds: float = Field(gt=.3, le=12)
    visual_role: Literal["hook", "journey", "detail", "payoff", "closing"]


class NarrativePlan(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    script: str = Field(min_length=1, max_length=2400)
    beats: list[NarrativeBeat] = Field(min_length=1, max_length=10)

