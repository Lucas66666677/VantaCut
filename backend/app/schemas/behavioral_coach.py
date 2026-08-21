from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class BehavioralCoachRequest(BaseModel):
    timeline_id: UUID | None = None


class BehavioralCoachTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str


class ApplyBehavioralCoachRequest(BaseModel):
    analysis_id: UUID
