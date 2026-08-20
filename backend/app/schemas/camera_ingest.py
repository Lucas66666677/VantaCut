"""Request/response contracts for the camera-to-cloud ingest gateway."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CameraMetadata(BaseModel):
    camera_model: str | None = Field(default=None, max_length=200)
    lens_model: str | None = Field(default=None, max_length=300)
    timecode: str | None = Field(default=None, max_length=64)
    gps_latitude: float | None = Field(default=None, ge=-90, le=90)
    gps_longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_altitude_m: float | None = Field(default=None, ge=-1000, le=100000)
    focal_length_mm: float | None = Field(default=None, gt=0, le=5000)
    aperture_f_number: float | None = Field(default=None, gt=0, le=128)
    shutter_seconds: float | None = Field(default=None, gt=0, le=60)
    iso: int | None = Field(default=None, ge=1, le=10_000_000)
    recording_started_at: datetime | None = None
    extra: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class RegisterCameraDeviceRequest(BaseModel):
    # user_id removed: the provisioning caller's identity is now derived
    # exclusively from the authenticated bearer token (current_user.id),
    # never client-supplied, alongside the existing management-token gate.
    project_id: UUID
    device_identifier: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    device_type: str = Field(default="camera", min_length=1, max_length=80)
    metadata: CameraMetadata = Field(default_factory=CameraMetadata)


class RegisterCameraDeviceResponse(BaseModel):
    device_id: UUID
    device_identifier: str
    device_secret: str = Field(description="Displayed once; provision into the camera or edge gateway securely.")


class StartCameraIngestRequest(BaseModel):
    # user_id removed: same reasoning as RegisterCameraDeviceRequest above.
    capture_id: str = Field(min_length=1, max_length=160)
    timeline_id: UUID | None = None
    metadata: CameraMetadata = Field(default_factory=CameraMetadata)


class CameraIngestSessionResponse(BaseModel):
    session_id: UUID
    project_id: UUID
    device_id: UUID
    timeline_id: UUID
    capture_id: str
    status: str
    upload_url_template: str
    hmac_headers: list[str]


class CompleteCameraIngestResponse(BaseModel):
    session_id: UUID
    status: str
    total_duration_seconds: float


class CameraChunkAcceptedResponse(BaseModel):
    chunk_id: UUID
    sequence_number: int
    status: str
    duplicate: bool = False
