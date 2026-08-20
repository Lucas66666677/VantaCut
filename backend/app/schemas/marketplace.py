from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PublishMarketplaceTemplateRequest(BaseModel):
    # creator_id removed: the publishing creator is now derived exclusively
    # from the authenticated caller (current_user.id), never client-supplied.
    template_id: UUID
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=180)
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    price_cents: int = Field(ge=50, le=100_000_00)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    safe_preview: dict[str, Any] = Field(default_factory=dict)
    # Never returned by an API. It is encrypted immediately and only decrypted by a trusted worker.
    private_payload: dict[str, Any]


class MarketplaceTemplateResponse(BaseModel):
    id: UUID
    template_id: UUID
    slug: str
    title: str
    summary: str | None
    price_cents: int
    currency: str
    status: str
    safe_preview: dict[str, Any]


class CreateLicenseRequest(BaseModel):
    # buyer_id removed: the buyer is now derived exclusively from the
    # authenticated caller (current_user.id), never client-supplied.
    project_id: UUID


class CheckoutResponse(BaseModel):
    license_id: UUID
    payment_intent_client_secret: str
    amount_cents: int
    currency: str


class ApplyLicenseRequest(BaseModel):
    # buyer_id removed: the buyer is now derived exclusively from the
    # authenticated caller (current_user.id), never client-supplied.
    timeline_id: UUID


class ApplyLicenseResponse(BaseModel):
    license_id: UUID
    timeline_id: UUID
    status: str
    blackbox_render_only: bool


# ConnectOnboardingRequest removed: start_connect_onboarding took no fields
# once creator_id (the only field it had) was removed — the creator is now
# derived exclusively from the authenticated caller (current_user.id), so
# the route no longer accepts a request body at all.


class ConnectOnboardingResponse(BaseModel):
    stripe_account_id: str
    onboarding_url: str


class DashboardPersona(BaseModel):
    segment: str
    users: int
    usage_count: int
    share_percent: float


class CreatorDashboardResponse(BaseModel):
    creator_id: UUID
    template_count: int
    successful_uses: int
    estimated_mrr_cents: int
    currency: str
    top_user_personas: list[DashboardPersona]
    payout_status: Literal["not_connected", "onboarding_required", "ready"]
