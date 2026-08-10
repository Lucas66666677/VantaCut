from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AutoDirectorCreateRequest(BaseModel):
    user_id: UUID
    topic: str = Field(min_length=8, max_length=1200)
    target_duration_seconds: int = Field(default=90, ge=20, le=900)
    language: str = Field(default="zh-TW", min_length=2, max_length=16)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16)$")
    tone: str = Field(default="cinematic, factual travel documentary", max_length=200)

    def creative_brief(self) -> dict[str, Any]:
        return {
            "target_duration_seconds": self.target_duration_seconds,
            "language": self.language,
            "aspect_ratio": self.aspect_ratio,
            "tone": self.tone,
        }


class AutoDirectorCreateResponse(BaseModel):
    run_id: UUID
    task_id: str
    status: str


class AutoDirectorRunResponse(BaseModel):
    id: UUID
    project_id: UUID
    topic: str
    status: str
    provider_name: str | None
    script: dict[str, Any]
    research: dict[str, Any]
    narration_key: str | None
    result_timeline_id: UUID | None
    message: str | None
