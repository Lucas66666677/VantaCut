from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TimeSeriesPoint(BaseModel):
    timestamp: datetime
    value: float = Field(allow_inf_nan=False)


class DataChartRequest(BaseModel):
    user_id: UUID
    title: str = Field(default="Market trend", min_length=1, max_length=120)
    points: list[TimeSeriesPoint] = Field(min_length=2, max_length=5000)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    width: int = Field(default=800, ge=240, le=1920)
    height: int = Field(default=420, ge=160, le=1080)
    fps: int = Field(default=60, ge=24, le=120)
    color: str = Field(default="#38BDF8", pattern=r"^#[0-9A-Fa-f]{6}$")
    x: float = Field(default=.04, ge=0, le=1)
    y: float = Field(default=.06, ge=0, le=1)
    template: Literal["line_chart"] = "line_chart"

    @model_validator(mode="after")
    def end_after_start(self) -> "DataChartRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if any(left.timestamp >= right.timestamp for left, right in zip(self.points, self.points[1:])):
            raise ValueError("points must be supplied in strictly ascending timestamp order")
        return self


class DataChartTaskResponse(BaseModel):
    chart_id: str
    task_id: str
    timeline_id: UUID
    status: str
