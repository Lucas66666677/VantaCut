from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class GenerateMusicRequest(BaseModel):
    user_id: UUID
    prompt: str = Field(min_length=3, max_length=800)
    instrumental_only: bool = True
    mix_level: float = Field(default=.16, ge=0, le=.9)
    provider: Literal["suno", "udio", "mock"] | None = None


class GenerateMusicResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
    target_duration_seconds: float


class GenerateMusicStatusResponse(BaseModel):
    status: str
    prompt: str | None = None
    target_duration_seconds: float | None = None
    audio_key: str | None = None
    instrumental_only: bool = True
    vocals_removed: bool = False
    provider_name: str | None = None
    finishing_mode: str | None = None
    error: str | None = None
