from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CloudDraftPayload(BaseModel):
    user_id: UUID
    timeline: dict[str, object]
    editor_state: dict[str, object] = Field(default_factory=dict)
    client_updated_at: datetime | None = None


class CloudDraftResponse(BaseModel):
    timeline_id: UUID
    status: str
    timeline: dict[str, object]
    editor_state: dict[str, object]
    updated_at: datetime | None = None


class MobilePreviewHandoffRequest(BaseModel):
    user_id: UUID


class MobilePreviewHandoffResponse(BaseModel):
    preview_url: str
    qr_code_data_uri: str
    expires_at: datetime


class MobilePreviewAsset(BaseModel):
    id: str
    url: str


class MobilePreviewManifest(BaseModel):
    timeline_id: UUID
    expires_at: datetime
    timeline: dict[str, object]
    assets: list[MobilePreviewAsset]

