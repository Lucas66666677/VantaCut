"""Versioned public Headless API for third-party video automation."""
from __future__ import annotations

import hmac
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.config import settings
from app.models.entities import PlatformAPIKey, PlatformInvoice, PlatformJob, User
from app.schemas.platform import (
    CreatePlatformAPIKeyRequest, HeadlessVideoRequest, PlatformAPIKeyCreatedResponse,
    PlatformAPIKeyResponse, PlatformInvoiceResponse, PlatformJobResponse,
)
from app.services.platform_metering import record_usage
from app.services.platform_security import (
    PlatformSecurityError, consume_request_token, encrypt_webhook_secret, hash_api_key,
    issue_api_key, mark_key_used, validate_public_url,
)
from app.tasks.platform_tasks import process_platform_job

router = APIRouter(prefix="/platform/v1", tags=["platform-v1"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_platform_management_token(value: str | None = Header(default=None, alias="X-Platform-Admin-Token")) -> None:
    """Replace with the app's authenticated admin/user scope dependency when available."""
    if not settings.platform_management_token:
        raise HTTPException(status_code=503, detail="Platform management is not configured")
    if not value or not hmac.compare_digest(value, settings.platform_management_token):
        raise HTTPException(status_code=403, detail="Platform management authorization failed")


def _key_response(key: PlatformAPIKey) -> PlatformAPIKeyResponse:
    return PlatformAPIKeyResponse(id=key.id, name=key.name, key_prefix=key.key_prefix, webhook_url=key.webhook_url, rate_limit_rps=float(key.rate_limit_rps), burst_limit=key.burst_limit, is_active=key.is_active, created_at=key.created_at)


def _job_response(job: PlatformJob) -> PlatformJobResponse:
    return PlatformJobResponse(id=job.id, operation=job.operation, status=job.status, created_at=job.created_at, result=dict(job.result_json or {}), error=job.error_message)  # type: ignore[arg-type]


def platform_api_key_dependency(raw_key: str | None = Depends(api_key_header), db: Session = Depends(get_db)) -> PlatformAPIKey:
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")
    try:
        key_hash = hash_api_key(raw_key)
        api_key = db.scalar(select(PlatformAPIKey).where(PlatformAPIKey.key_hash == key_hash))
        if api_key is None or not api_key.is_active:
            raise HTTPException(status_code=401, detail="Invalid API key")
        allowed, retry_after = consume_request_token(api_key)
    except PlatformSecurityError as exc:
        raise HTTPException(status_code=503, detail="Platform authentication is not configured") from exc
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded", headers={"Retry-After": str(retry_after)})
    mark_key_used(api_key)
    record_usage(db, api_key_id=api_key.id, metric="api_requests", quantity=1, dimensions={"surface": "platform_v1"})
    db.commit()
    return api_key


@router.post("/api-keys", response_model=PlatformAPIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_platform_api_key(payload: CreatePlatformAPIKeyRequest, _: None = Depends(require_platform_management_token), db: Session = Depends(get_db)) -> PlatformAPIKeyCreatedResponse:
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        webhook_url = validate_public_url(str(payload.webhook_url)) if payload.webhook_url else None
        raw_key, prefix, key_hash = issue_api_key()
        # Returned once only; DB receives the Fernet-encrypted representation.
        webhook_signing_secret = secrets.token_urlsafe(32)
        webhook_secret = encrypt_webhook_secret(webhook_signing_secret)
    except PlatformSecurityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    key = PlatformAPIKey(owner_id=payload.user_id, name=payload.name, key_prefix=prefix, key_hash=key_hash, webhook_url=webhook_url, encrypted_webhook_secret=webhook_secret, rate_limit_rps=payload.rate_limit_rps, burst_limit=payload.burst_limit)
    db.add(key); db.commit(); db.refresh(key)
    return PlatformAPIKeyCreatedResponse(id=key.id, name=key.name, key_prefix=key.key_prefix, api_key=raw_key, webhook_signing_secret=webhook_signing_secret, webhook_url=key.webhook_url, rate_limit_rps=float(key.rate_limit_rps), burst_limit=key.burst_limit)


@router.get("/api-keys", response_model=list[PlatformAPIKeyResponse])
def list_platform_api_keys(user_id: UUID, _: None = Depends(require_platform_management_token), db: Session = Depends(get_db)) -> list[PlatformAPIKeyResponse]:
    if db.get(User, user_id) is None: raise HTTPException(status_code=404, detail="User not found")
    return [_key_response(key) for key in db.scalars(select(PlatformAPIKey).where(PlatformAPIKey.owner_id == user_id).order_by(PlatformAPIKey.created_at.desc())).all()]


@router.delete("/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_platform_api_key(api_key_id: UUID, user_id: UUID, _: None = Depends(require_platform_management_token), db: Session = Depends(get_db)) -> None:
    key = db.get(PlatformAPIKey, api_key_id)
    if key is None: raise HTTPException(status_code=404, detail="API key not found")
    if key.owner_id != user_id: raise HTTPException(status_code=403, detail="User cannot revoke this API key")
    key.is_active = False; db.commit()


def _create_job(operation: str, payload: HeadlessVideoRequest, idempotency_key: str | None, api_key: PlatformAPIKey, db: Session) -> PlatformJobResponse:
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required and must be <= 255 characters")
    try:
        source_url = validate_public_url(str(payload.source_url))
        webhook_url = validate_public_url(str(payload.webhook_url)) if payload.webhook_url else api_key.webhook_url
    except PlatformSecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = db.scalar(select(PlatformJob).where(PlatformJob.api_key_id == api_key.id, PlatformJob.idempotency_key == idempotency_key).with_for_update())
    if existing is not None:
        if existing.operation != operation or existing.source_url != source_url:
            raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different request")
        return _job_response(existing)
    job = PlatformJob(api_key_id=api_key.id, idempotency_key=idempotency_key, operation=operation, source_url=source_url, request_json={"instructions": payload.instructions}, webhook_url=webhook_url)
    db.add(job); db.commit(); db.refresh(job)
    try:
        process_platform_job.delay(str(job.id))
    except Exception as exc:
        job.status, job.error_message = "failed", f"Unable to enqueue platform job: {exc}"; db.commit()
        raise HTTPException(status_code=503, detail="Platform worker queue is unavailable") from exc
    return _job_response(job)


@router.post("/videos/rough-cut", response_model=PlatformJobResponse, status_code=status.HTTP_202_ACCEPTED)
def headless_rough_cut(payload: HeadlessVideoRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), api_key: PlatformAPIKey = Depends(platform_api_key_dependency), db: Session = Depends(get_db)) -> PlatformJobResponse:
    return _create_job("rough_cut", payload, idempotency_key, api_key, db)


@router.post("/videos/render", response_model=PlatformJobResponse, status_code=status.HTTP_202_ACCEPTED)
def headless_render(payload: HeadlessVideoRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), api_key: PlatformAPIKey = Depends(platform_api_key_dependency), db: Session = Depends(get_db)) -> PlatformJobResponse:
    return _create_job("render", payload, idempotency_key, api_key, db)


@router.get("/jobs/{job_id}", response_model=PlatformJobResponse)
def get_platform_job(job_id: UUID, api_key: PlatformAPIKey = Depends(platform_api_key_dependency), db: Session = Depends(get_db)) -> PlatformJobResponse:
    job = db.get(PlatformJob, job_id)
    if job is None or job.api_key_id != api_key.id: raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


@router.get("/billing/invoices", response_model=list[PlatformInvoiceResponse])
def list_platform_invoices(user_id: UUID, _: None = Depends(require_platform_management_token), db: Session = Depends(get_db)) -> list[PlatformInvoiceResponse]:
    keys = db.scalars(select(PlatformAPIKey).where(PlatformAPIKey.owner_id == user_id)).all()
    invoices = db.scalars(select(PlatformInvoice).where(PlatformInvoice.api_key_id.in_([key.id for key in keys])).order_by(PlatformInvoice.period_start.desc())).all() if keys else []
    return [PlatformInvoiceResponse(id=item.id, period_start=item.period_start, period_end=item.period_end, status=item.status, totals=dict(item.totals_json or {})) for item in invoices]
