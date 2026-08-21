from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class NormalizedMaskBox(BaseModel):
    x: float = Field(ge=0, lt=1, description="Left edge as a fraction of frame width")
    y: float = Field(ge=0, lt=1, description="Top edge as a fraction of frame height")
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_frame(self) -> "NormalizedMaskBox":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Mask box must stay within the source frame")
        return self


class NormalizedMaskPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class NormalizedBrushStroke(BaseModel):
    points: list[NormalizedMaskPoint] = Field(min_length=1, max_length=600)
    radius: float = Field(default=.035, gt=0, le=.25, description="Brush radius as a fraction of the short image edge")


class VideoInpaintingRequest(BaseModel):
    frame_time: float = Field(ge=0, description="Source-video time of the user annotation in seconds")
    mask_box: NormalizedMaskBox | None = None
    mask_strokes: list[NormalizedBrushStroke] = Field(default_factory=list, max_length=80)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    before_seconds: float = Field(default=3, ge=0, le=10)
    after_seconds: float = Field(default=3, ge=0, le=10)
    use_proxy: bool = True

    @model_validator(mode="after")
    def requires_mask_and_valid_window(self) -> "VideoInpaintingRequest":
        if self.mask_box is None and not self.mask_strokes:
            raise ValueError("Provide mask_box or at least one brush stroke")
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        if self.start_time is not None and not self.start_time <= self.frame_time:
            raise ValueError("frame_time must lie within the repair window")
        if self.end_time is not None and not self.frame_time <= self.end_time:
            raise ValueError("frame_time must lie within the repair window")
        return self


class VideoInpaintingTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str
