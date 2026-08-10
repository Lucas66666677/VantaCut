"""Contracts for editor semantic snapping guides."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


SemanticSnapType = Literal["downbeat", "speech_pause", "action_peak"]


class SemanticSnapPoint(BaseModel):
    id: str
    time_seconds: float = Field(ge=0)
    type: SemanticSnapType
    strength: float = Field(ge=0, le=1)
    label: str
    source_asset_id: UUID | None = None


class SemanticSnapPointsResponse(BaseModel):
    timeline_id: UUID
    points: list[SemanticSnapPoint]
    available_types: list[SemanticSnapType] = ["downbeat", "speech_pause", "action_peak"]

