from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.entities import MediaStatus, MediaType


class UploadURLRequest(BaseModel):
    project_id: UUID
    filename: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(gt=0)
    content_type: str = Field(default="video/mp4", max_length=120)
    media_type: MediaType = MediaType.VIDEO


class UploadURLResponse(BaseModel):
    asset_id: UUID
    storage_key: str
    upload_url: str
    expires_in: int
    required_headers: dict[str, str]


class ConfirmUploadRequest(BaseModel):
    asset_id: UUID


class MultipartUploadInitiateResponse(BaseModel):
    asset_id: UUID
    storage_key: str
    upload_id: str
    part_size_bytes: int = 16 * 1024 * 1024
    expires_in: int


class MultipartPartURLRequest(BaseModel):
    asset_id: UUID
    upload_id: str = Field(min_length=1)
    part_number: int = Field(ge=1, le=10_000)


class MultipartPartURLResponse(BaseModel):
    upload_url: str
    required_headers: dict[str, str] = {}


class MultipartUploadPart(BaseModel):
    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1)


class MultipartCompleteRequest(BaseModel):
    asset_id: UUID
    upload_id: str = Field(min_length=1)
    parts: list[MultipartUploadPart] = Field(min_length=1)


class MediaAssetResponse(BaseModel):
    id: UUID
    project_id: UUID
    filename: str
    storage_key: str
    status: MediaStatus
    size_bytes: int | None
    created_at: datetime

    model_config = {"from_attributes": True}
