from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AutoPipRequest(BaseModel):
    main_asset_id: UUID
    selfie_asset_id: UUID
    corner: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "bottom_right"
    focus_after_seconds: float = Field(default=3.0, ge=1, le=15)


class AutoPipResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class OverlayPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class AutoPipOverlayRequest(BaseModel):
    kind: Literal["highlighter", "arrow"]
    points: list[OverlayPoint] = Field(min_length=2, max_length=512)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    color: str = Field(default="#eaff3b", max_length=16)
    width: float = Field(default=7, ge=1, le=36)


class AutoPipOverlayResponse(BaseModel):
    overlay_id: str
    status: str
