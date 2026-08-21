from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.ai.providers.schemas import WordTimestamp


class ConfirmedTimelineSegment(BaseModel):
    id: UUID | None = None
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    action: Literal["keep", "remove"]
    confidence_score: int = Field(ge=0, le=100)
    reason: str

    @field_validator("source_end")
    @classmethod
    def end_must_follow_start(cls, value: float, info) -> float:
        start = info.data.get("source_start")
        if start is not None and value <= start:
            raise ValueError("source_end must be greater than source_start")
        return value


class GenerateSubtitlesRequest(BaseModel):
    source_asset_id: UUID
    segments: list[ConfirmedTimelineSegment] = Field(min_length=1)
    language: str | None = Field(default=None, max_length=20)
    target_language: str | None = Field(default=None, max_length=20)


class SubtitleCue(BaseModel):
    id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    text: str
    words: list[WordTimestamp] = Field(default_factory=list)
    animation_preset: Literal["none", "spring", "pop", "shake", "explode", "float"] = "none"
    emotion: Literal["neutral", "emphasis", "surprise", "anger", "joy", "sadness"] = "neutral"


class SubtitleGenerationResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class GenerateBilingualSubtitlesRequest(BaseModel):
    target_language: str = Field(default="en", min_length=2, max_length=20)
    source_language: str | None = Field(default=None, max_length=20)


class SubtitleExportResponse(BaseModel):
    format: Literal["srt", "vtt"]
    track: Literal["bilingual", "source", "target"]
    language: str | None = None
    download_url: str


class CaptionStyleRequest(BaseModel):
    preset: Literal["viral_yellow", "karaoke_pop", "clean_white"] = "viral_yellow"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
