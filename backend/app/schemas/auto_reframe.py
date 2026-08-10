from uuid import UUID

from pydantic import BaseModel, Field


class AutoReframeRequest(BaseModel):
    user_id: UUID
    detector_stride: int = Field(default=2, ge=1, le=12)
    smoothing: float = Field(default=.75, ge=0, le=1)
    max_pan_speed_px_per_second: float = Field(default=720, gt=20, le=5000)


class AutoReframeResponse(BaseModel):
    timeline_id: UUID
    status: str
    settings: dict[str, object]
