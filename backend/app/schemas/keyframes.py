from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class CubicBezier(BaseModel):
    x1: float = Field(default=.42, ge=0, le=1)
    y1: float = Field(default=0, ge=-2, le=2)
    x2: float = Field(default=.58, ge=0, le=1)
    y2: float = Field(default=1, ge=-2, le=2)


class TransformValue(BaseModel):
    x: float = Field(default=.5, ge=-2, le=3)
    y: float = Field(default=.5, ge=-2, le=3)
    scale: float = Field(default=1, gt=.01, le=8)
    rotation_degrees: float = Field(default=0, ge=-3600, le=3600)
    z: float = Field(default=0, ge=-4, le=4)


class TransformKeyframe(BaseModel):
    time: float = Field(ge=0)
    value: TransformValue
    easing: Literal["linear", "ease-in-out", "cubic-bezier"] = "ease-in-out"
    cubic_bezier: CubicBezier | None = None

    @model_validator(mode="after")
    def bezier_for_custom_easing(self) -> "TransformKeyframe":
        if self.easing == "cubic-bezier" and self.cubic_bezier is None:
            raise ValueError("cubic_bezier is required for cubic-bezier easing")
        return self


class ClipTransformAnimation(BaseModel):
    clip_id: UUID | None = None
    keyframes: list[TransformKeyframe] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def sorted_times(self) -> "ClipTransformAnimation":
        if any(right.time <= left.time for left, right in zip(self.keyframes, self.keyframes[1:])):
            raise ValueError("Keyframes must be strictly ordered by time")
        return self


class TimelineKeyframeUpdateRequest(BaseModel):
    animations: list[ClipTransformAnimation] = Field(min_length=1, max_length=100)


class TimelineKeyframeUpdateResponse(BaseModel):
    timeline_id: UUID
    status: str
    animations: list[dict]
