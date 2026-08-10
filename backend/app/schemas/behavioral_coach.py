from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class BehavioralCoachRequest(BaseModel):
    user_id: UUID
    timeline_id: UUID | None = None


class BehavioralCoachTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str


class ApplyBehavioralCoachRequest(BaseModel):
    user_id: UUID
    analysis_id: UUID
