from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


NudgeOperation = Literal["adjust_visual", "set_speed_curve", "set_transform", "enable_beat_sync"]


class NudgeCommand(BaseModel):
    operation: NudgeOperation
    target_clip_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class NudgeRequest(BaseModel):
    user_id: UUID
    instruction: str = Field(min_length=1, max_length=600)
    target_clip_ids: list[str] = Field(default_factory=list, max_length=100)


class NudgeResponse(BaseModel):
    timeline_id: UUID
    provider_name: str
    commands: list[NudgeCommand]
    explanation: str
