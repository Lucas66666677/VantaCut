from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class BezierPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class FinanceAnnotation(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["support", "resistance"]
    p0: BezierPoint
    p1: BezierPoint
    p2: BezierPoint
    p3: BezierPoint
    label: str = Field(max_length=80)


class FinanceTrackRequest(BaseModel):
    user_id: UUID
    symbol: str = Field(pattern=r"^[A-Za-z0-9.^=-]{1,20}$")
    market: Literal["twse", "yahoo_compatible"] = "twse"
    history_start: date
    history_end: date
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    width: int = Field(default=960, ge=320, le=1920)
    height: int = Field(default=540, ge=180, le=1080)
    fps: int = Field(default=30, ge=24, le=60)
    x: float = Field(default=.04, ge=0, le=1)
    y: float = Field(default=.06, ge=0, le=1)
    indicators: list[Literal["sma20", "sma60", "rsi14", "macd"]] = Field(default_factory=lambda: ["sma20", "rsi14", "macd"])
    annotations: list[FinanceAnnotation] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_range(self) -> "FinanceTrackRequest":
        if self.history_end < self.history_start or self.end_time <= self.start_time:
            raise ValueError("history and Timeline end times must follow their starts")
        if (self.history_end - self.history_start).days > 3660:
            raise ValueError("History range is limited to ten years per render track")
        return self


class FinanceTrackResponse(BaseModel):
    finance_track_id: str
    task_id: str
    timeline_id: UUID
    status: str


class FinanceAnnotationsUpdate(BaseModel):
    user_id: UUID
    annotations: list[FinanceAnnotation] = Field(default_factory=list, max_length=20)
