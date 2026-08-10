"""Request and response contracts for the low-latency live director."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class CreateLiveSessionRequest(BaseModel):
    user_id: UUID
    project_id: UUID
    title: str = Field(min_length=1, max_length=160)
    output_rtmp_url: str | None = Field(
        default=None,
        description="YouTube/Twitch RTMP(S) ingest URL. Never return this value to a browser.",
    )
    wide_camera_id: str | None = Field(default=None, max_length=80)
    width: int = Field(default=1280, ge=640, le=3840)
    height: int = Field(default=720, ge=360, le=2160)
    fps: int = Field(default=30, ge=15, le=60)


class LiveSessionResponse(BaseModel):
    session_id: str
    project_id: UUID
    status: Literal["created", "live", "stopped", "failed"]
    obs_publish_url_template: str
    phone_offer_path_template: str
    attach_gateway_source_path: str
    control_websocket_path: str


class WebRTCOfferRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    sdp: str = Field(min_length=1)
    type: Literal["offer"]
    is_wide_camera: bool = False


class WebRTCAnswerResponse(BaseModel):
    sdp: str
    type: Literal["answer"]


class AttachGatewaySourceRequest(BaseModel):
    """Attach a source already published to MediaMTX by OBS/FFmpeg.

    The caller sends a camera ID only; the server derives the RTSP URL, preventing
    SSRF through arbitrary media URLs.
    """

    camera_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    is_wide_camera: bool = False


class LiveCaptionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    emotion: Literal["neutral", "emphasis", "surprise", "anger", "joy", "sadness"] = "neutral"
    animation_preset: Literal["none", "spring", "pop", "shake", "explode", "float"] = "pop"
    ttl_seconds: float = Field(default=2.8, ge=0.5, le=12)


class LiveDirectorOverride(BaseModel):
    layout: Literal["auto", "single", "split", "wide"] = "auto"
    camera_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def single_requires_camera(self) -> "LiveDirectorOverride":
        if self.layout == "single" and not self.camera_id:
            raise ValueError("camera_id is required when layout is single")
        return self


class LiveSessionStatus(BaseModel):
    session_id: str
    status: Literal["created", "live", "stopped", "failed"]
    layout: Literal["single", "split", "wide"]
    active_camera_id: str | None = None
    sources: list[dict[str, object]] = Field(default_factory=list)
    caption: dict[str, object] | None = None
