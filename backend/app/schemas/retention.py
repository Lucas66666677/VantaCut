"""Public contracts for pre-export retention estimation."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RetentionPredictionRequest(BaseModel):
    refresh: bool = False


class RetentionCurvePoint(BaseModel):
    time_seconds: float = Field(ge=0)
    expected_retention: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100)


class RetentionHotspot(BaseModel):
    id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    predicted_drop: float = Field(gt=0, le=100)
    risk_score: float = Field(ge=0, le=100)
    reason: str
    suggestion: str
    feature_evidence: dict[str, float] = Field(default_factory=dict)


class RetentionPredictionResponse(BaseModel):
    timeline_id: UUID
    model_name: str
    prediction_mode: str = Field(description="checkpoint or heuristic_baseline")
    is_calibrated: bool
    window_seconds: float
    curve: list[RetentionCurvePoint]
    hotspots: list[RetentionHotspot]
    summary: str
