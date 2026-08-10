from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, HttpUrl, field_validator


class CreatePlatformAPIKeyRequest(BaseModel):
    user_id: UUID
    name: str = Field(min_length=1, max_length=160)
    webhook_url: AnyHttpUrl | None = None
    rate_limit_rps: float = Field(default=2.0, gt=0, le=500)
    burst_limit: int = Field(default=10, ge=1, le=5000)


class PlatformAPIKeyCreatedResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    api_key: str
    webhook_signing_secret: str
    webhook_url: str | None
    rate_limit_rps: float
    burst_limit: int


class PlatformAPIKeyResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    webhook_url: str | None
    rate_limit_rps: float
    burst_limit: int
    is_active: bool
    created_at: datetime


class HeadlessVideoRequest(BaseModel):
    source_url: HttpUrl
    instructions: dict[str, Any] = Field(default_factory=dict)
    webhook_url: AnyHttpUrl | None = None

    @field_validator("instructions")
    @classmethod
    def limit_instruction_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 128_000:
            raise ValueError("instructions exceeds 128KB")
        return value


class PlatformJobResponse(BaseModel):
    id: UUID
    operation: Literal["rough_cut", "render"]
    status: str
    created_at: datetime
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class PlatformInvoiceResponse(BaseModel):
    id: UUID
    period_start: datetime
    period_end: datetime
    status: str
    totals: dict[str, Any]
