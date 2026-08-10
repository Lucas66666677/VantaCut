from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class BRollGenerationRequest(BaseModel):
    user_id: UUID
    source_asset_id: UUID
    output_start: float | None = Field(default=None, ge=0, description="Final-timeline position. Omit to select an information-dense, visually static moment.")
    duration_seconds: float = Field(default=4, ge=3, le=5)
    provider: Literal["sora", "runway"] | None = None
    prompt_override: str | None = Field(default=None, min_length=12, max_length=1200)
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"


class VideoOutpaintRequest(BaseModel):
    user_id: UUID
    media_asset_id: UUID
    target_aspect_ratio: Literal["9:16"] = "9:16"
    start_time: float = Field(default=0, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    use_proxy: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> "VideoOutpaintRequest":
        if self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class VideoGenerationTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str
