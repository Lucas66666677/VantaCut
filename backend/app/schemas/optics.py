from uuid import UUID

from pydantic import BaseModel, Field


class OpticsAnalysisRequest(BaseModel):
    user_id: UUID


class OpticalFlowRetimeRequest(BaseModel):
    user_id: UUID
    slow_motion_factor: float = Field(gt=1, le=8)
    apply_motion_blur: bool = False
    use_proxy: bool = True


class OpticalLookRequest(BaseModel):
    user_id: UUID
    chromatic_aberration_px: float = Field(default=1.2, ge=0, le=12)
    vignette_strength: float = Field(default=.35, ge=0, le=1)
    vignette_power: float = Field(default=2.2, ge=.1, le=8)
    bokeh_radius_px: float = Field(default=12, ge=0, le=61)
    focus_depth: float = Field(default=.5, gt=0, le=1)
    aperture_f_number: float = Field(default=2, ge=.7, le=32)
    focal_length_mm: float | None = Field(default=None, gt=0, le=1200)
    sensor_width_mm: float = Field(default=36, gt=1, le=100)
    focus_distance_m: float | None = Field(default=None, gt=.05, le=10_000)
    horizontal_fov_degrees: float | None = Field(default=None, gt=1, lt=179)


class OpticalTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str
