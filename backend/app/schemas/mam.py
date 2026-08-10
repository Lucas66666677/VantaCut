from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectStorageActor(BaseModel):
    user_id: UUID


class HydrateProjectRequest(ProjectStorageActor):
    media_asset_ids: list[UUID] | None = Field(default=None, max_length=500)


class HydrationResponse(BaseModel):
    hydration_job_id: UUID | None
    status: str
    progress: int
    estimated_ready_at: datetime | None
    message: str


class ArchivedAssetStatus(BaseModel):
    asset_id: UUID
    filename: str
    archive_status: str
    proxy_available: bool
    restore_expires_at: datetime | None


class ProjectStorageStatusResponse(BaseModel):
    project_id: UUID
    lifecycle_state: str
    proxy_playback_available: bool
    high_quality_render_ready: bool
    active_hydration: HydrationResponse | None
    assets: list[ArchivedAssetStatus]
