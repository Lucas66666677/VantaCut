from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MattingPoint(BaseModel):
    x: float = Field(ge=0, le=1, description="Horizontal normalized click position")
    y: float = Field(ge=0, le=1, description="Vertical normalized click position")
    positive: bool = True


class VideoMattingRequest(BaseModel):
    user_id: UUID
    mode: Literal["click", "text"]
    frame_time: float = Field(default=0, ge=0)
    points: list[MattingPoint] = Field(default_factory=list, max_length=16)
    text_prompt: str | None = Field(default=None, min_length=2, max_length=180)
    use_proxy: bool = True
    feather_pixels: float = Field(default=2.5, ge=0, le=24)
    despill_strength: float = Field(default=.65, ge=0, le=1)

    @model_validator(mode="after")
    def validate_prompt_mode(self) -> "VideoMattingRequest":
        if self.mode == "click" and not any(point.positive for point in self.points):
            raise ValueError("Click matting requires at least one positive point")
        if self.mode == "text" and not self.text_prompt:
            raise ValueError("Text matting requires text_prompt")
        return self


class VideoMattingTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str
