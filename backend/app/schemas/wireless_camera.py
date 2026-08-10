"""Contracts for browser-to-editor wireless multi-camera recording."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateWirelessCameraPairingRequest(BaseModel):
    user_id: UUID
    label: str = Field(default="無線鏡頭", min_length=1, max_length=80)


class WirelessCameraPairingResponse(BaseModel):
    pairing_id: UUID
    session_id: UUID
    timeline_id: UUID
    label: str
    camera_index: int
    mobile_url: str
    qr_code_data_uri: str
    expires_at: datetime
    server_epoch_ms: int
    capture_origin_ms: int


class WirelessCameraStartRequest(BaseModel):
    # The phone first measures its clock skew using ``server_epoch_ms`` from the
    # pairing response, then submits a server-aligned wall clock timestamp.
    server_aligned_started_at_ms: int = Field(ge=0)


class WirelessCameraStartResponse(BaseModel):
    session_id: UUID
    timeline_offset_seconds: float
    capture_origin_ms: int


class WirelessCameraClockResponse(BaseModel):
    server_epoch_ms: int
    capture_origin_ms: int


class WirelessCameraChunkResponse(BaseModel):
    chunk_id: UUID
    sequence_number: int
    status: str
    duplicate: bool = False


class WirelessCameraCompleteResponse(BaseModel):
    session_id: UUID
    status: Literal["completed"]
    timeline_offset_seconds: float


class WirelessCameraPairingStatus(BaseModel):
    pairing_id: UUID
    label: str
    camera_index: int
    status: str
    session_id: UUID
