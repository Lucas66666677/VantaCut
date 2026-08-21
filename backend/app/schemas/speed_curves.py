from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SpeedCurvePoint(BaseModel):
    position: float = Field(ge=0, le=1, description="Normalised position within the source clip")
    speed: float = Field(ge=.1, le=10)


class ClipSpeedCurve(BaseModel):
    clip_id: UUID
    preset: Literal["hero", "flash_in", "montage", "custom"] = "custom"
    points: list[SpeedCurvePoint] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def complete_curve(self) -> "ClipSpeedCurve":
        if self.points[0].position != 0 or self.points[-1].position != 1:
            raise ValueError("Speed curve must begin at 0 and end at 1")
        if any(right.position <= left.position for left, right in zip(self.points, self.points[1:])):
            raise ValueError("Speed curve points must be strictly ordered")
        return self


class TimelineSpeedCurveUpdateRequest(BaseModel):
    curves: list[ClipSpeedCurve] = Field(min_length=1, max_length=100)


class TimelineSpeedCurveUpdateResponse(BaseModel):
    timeline_id: UUID
    status: str
    curves: list[dict]

