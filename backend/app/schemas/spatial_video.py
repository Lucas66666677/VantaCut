from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SpatialVideoExportRequest(BaseModel):
    source_render_job_id: UUID
    ipd_mm: float = Field(default=63.5, ge=50, le=75)
    horizontal_fov_degrees: float = Field(default=80, gt=35, lt=130)
    virtual_depth_range_m: float = Field(default=3.0, gt=.25, le=30)
    max_disparity_px: float = Field(default=28, ge=2, le=96)
    depth_model: Literal["auto", "depth_anything", "midas_small"] = "auto"
    temporal_depth_smoothing: float = Field(default=.72, ge=0, lt=1)


class SpatialVideoExportResponse(BaseModel):
    spatial_video_job_id: UUID
    task_id: str
    status: str
