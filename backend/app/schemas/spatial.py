from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SpatialReconstructionRequest(BaseModel):
    user_id: UUID
    frame_rate: float = Field(default=2, ge=.5, le=8)
    max_frames: int = Field(default=500, ge=30, le=1_500)
    iterations: int = Field(default=30_000, ge=1_000, le=100_000)
    use_proxy: bool = False


class VirtualCameraKeyframe(BaseModel):
    time_seconds: float = Field(ge=0)
    position: tuple[float, float, float]
    look_at: tuple[float, float, float]
    fov_degrees: float = Field(default=55, ge=20, le=110)


class VirtualCameraRenderRequest(BaseModel):
    user_id: UUID
    camera_path: list[VirtualCameraKeyframe] = Field(min_length=2, max_length=120)
    fps: int = Field(default=30, ge=12, le=60)
    width: int = Field(default=1920, ge=640, le=3840)
    height: int = Field(default=1080, ge=640, le=3840)

    @model_validator(mode="after")
    def chronological_path(self) -> "VirtualCameraRenderRequest":
        if any(later.time_seconds <= earlier.time_seconds for earlier, later in zip(self.camera_path, self.camera_path[1:])):
            raise ValueError("Virtual camera keyframes must be strictly chronological")
        return self


class SpatialTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str
