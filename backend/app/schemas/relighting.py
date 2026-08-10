from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class VirtualLightPayload(BaseModel):
    enabled: bool = True
    screen_x: float = Field(default=.5, ge=0, le=1)
    screen_y: float = Field(default=.25, ge=0, le=1)
    depth: float = Field(default=.2, ge=0, le=1)
    intensity: float = Field(default=1, ge=0, le=8)
    color_temperature_kelvin: int = Field(default=5600, ge=1000, le=20000)
    radius: float = Field(default=.35, gt=0, le=2)
    volumetric_strength: float = Field(default=.12, ge=0, le=2)
    shadow_strength: float = Field(default=.35, ge=0, le=1)


class RelightingAnalysisRequest(BaseModel):
    user_id: UUID
    depth_model: Literal["auto", "depth_anything", "midas_small"] = "auto"
    frame_stride: int = Field(default=1, ge=1, le=30)
    use_proxy: bool = False


class VirtualRelightTimelineRequest(BaseModel):
    user_id: UUID
    enabled: bool = True
    depth_model: Literal["auto", "depth_anything", "midas_small"] = "auto"
    temporal_depth_smoothing: float = Field(default=.72, ge=0, lt=1)
    ambient_strength: float = Field(default=0, ge=-1, le=2)
    lights: list[VirtualLightPayload] = Field(default_factory=lambda: [VirtualLightPayload()], min_length=1, max_length=4)


class RelightingTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str


class VirtualRelightTimelineResponse(BaseModel):
    timeline_id: UUID
    status: str
    settings: dict[str, object]
