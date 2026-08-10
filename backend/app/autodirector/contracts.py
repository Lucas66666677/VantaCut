from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DocumentaryBeat(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    purpose: Literal["hook", "context", "journey", "insight", "closing"]
    narration: str = Field(min_length=8, max_length=1200)
    visual_query: str = Field(min_length=3, max_length=300)
    target_duration_seconds: float = Field(ge=4, le=90)


class DocumentaryScript(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    summary: str = Field(min_length=10, max_length=1000)
    total_duration_seconds: float = Field(ge=20, le=900)
    beats: list[DocumentaryBeat] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_beat_duration(self) -> "DocumentaryScript":
        total = sum(beat.target_duration_seconds for beat in self.beats)
        if abs(total - self.total_duration_seconds) > max(8, self.total_duration_seconds * 0.2):
            raise ValueError("Beat durations must approximately equal total_duration_seconds")
        return self


class RetrievedMediaSegment(BaseModel):
    media_asset_id: UUID
    project_id: UUID
    filename: str
    source_start: float = Field(ge=0)
    source_end: float = Field(ge=0)
    modality: Literal["keyframe", "transcript"]
    similarity_score: float = Field(ge=0, le=1)
    matched_text: str | None = None
    duration_seconds: float | None = None


class BeatResearchResult(BaseModel):
    beat_id: str
    query: str
    candidates: list[RetrievedMediaSegment] = Field(default_factory=list)


class NarrationArtifact(BaseModel):
    storage_key: str
    duration_seconds: float = Field(gt=0)
    language: str
    text: str
