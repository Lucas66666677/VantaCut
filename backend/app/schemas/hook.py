"""Contracts for the pre-export first-three-seconds Hook health check."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class HookCheckRequest(BaseModel):
    user_id: UUID


class HookMetric(BaseModel):
    label: str
    value: str
    passed: bool


class HookReport(BaseModel):
    timeline_id: UUID
    score: int = Field(ge=0, le=100)
    traffic_light: Literal["green", "yellow", "red"]
    cut_rate_per_second: float = Field(ge=0)
    visual_motion_score: float = Field(ge=0, le=100)
    has_kinetic_captions: bool
    has_voice: bool
    has_audio_impact: bool
    is_static_opening: bool
    metrics: list[HookMetric]
    warnings: list[str]
    suggestions: list[str]
    highlight_candidate: dict[str, float | str]


class HookRescueRequest(BaseModel):
    user_id: UUID


class HookRescueResponse(BaseModel):
    source_timeline_id: UUID
    timeline_id: UUID
    status: Literal["applied"]
    inserted_duration_seconds: float
    message: str
