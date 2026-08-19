from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ComputeNodeEnrollRequest(BaseModel):
    # owner_id was previously a client-supplied field trusted as the node's
    # owner with no verified identity at all. It's removed here rather than
    # kept-but-ignored: no frontend caller sends it (confirmed by search),
    # so there is no compatibility reason to keep it, and keeping an unused
    # field around invites a future caller reintroducing the same trust bug.
    # The authoritative owner is now the enrollment endpoint's
    # get_current_user dependency (see distributed_compute.py).
    label: str = Field(min_length=1, max_length=160)
    public_key: str = Field(min_length=40, max_length=512, description="Base64 raw Ed25519 public key")
    node_kind: Literal["browser", "desktop"] = "browser"
    capabilities: dict[str, Any] = Field(default_factory=dict)
    consent: dict[str, Any] = Field(default_factory=dict)
    renderer_image_digest: str | None = Field(default=None, max_length=255)

    @field_validator("consent")
    @classmethod
    def explicit_consent_required(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("explicit_opt_in") is not True:
            raise ValueError("explicit_opt_in=true is required to join the compute pool")
        return value


class ComputeNodeResponse(BaseModel):
    node_id: UUID
    status: str
    credits_earned: int = 0


class ComputeNodeHeartbeatRequest(BaseModel):
    available: bool
    capabilities: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(min_length=32)
    signed_at: datetime


class DecentralizeRenderRequest(BaseModel):
    owner_id: UUID
    chunk_seconds: int = Field(default=5, ge=2, le=20)
    replication_factor: int = Field(default=2, ge=2, le=3)
    resolution: Literal["1080p", "4k", "8k"] = "4k"
    container_format: Literal["mp4", "mov"] = "mp4"


class DistributedBatchResponse(BaseModel):
    batch_id: UUID
    status: str
    chunk_count: int
    manifest_sha256: str


class AssignmentResultRequest(BaseModel):
    assignment_ticket: str
    output_object_key: str
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decoded_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    renderer_image_digest: str = Field(min_length=12, max_length=255)
    signature: str = Field(min_length=32)


class AssignmentResponse(BaseModel):
    assignment_id: UUID | None = None
    status: Literal["assigned", "idle"]
    ticket: str | None = None
    manifest: dict[str, Any] | None = None
    result_upload_key: str | None = None
    result_upload_url: str | None = None
    expires_at: datetime | None = None
