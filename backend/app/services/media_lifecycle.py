"""Policy layer for raw-footage cold storage, Glacier hydration, and free-tier retention."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Iterable
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.entities import (
    MediaAsset, MediaHydrationItem, MediaHydrationJob, Project, StorageRetentionNotice,
    SubscriptionTier, User,
)
from app.services.storage import object_archive_info


ARCHIVED_CLASSES = {"DEEP_ARCHIVE", "GLACIER"}


def is_restore_complete(restore_header: str | None) -> bool:
    return bool(restore_header and 'ongoing-request="false"' in restore_header)


def is_restore_in_progress(restore_header: str | None) -> bool:
    return bool(restore_header and 'ongoing-request="true"' in restore_header)


def parse_restore_expiry(restore_header: str | None) -> datetime | None:
    if not restore_header or "expiry-date=" not in restore_header:
        return None
    try:
        date_value = restore_header.split("expiry-date=", 1)[1].strip('"').split('"', 1)[0]
        return parsedate_to_datetime(date_value).astimezone(UTC)
    except (IndexError, TypeError, ValueError):
        return None


def archive_candidates(db: Session) -> list[MediaAsset]:
    cutoff = datetime.now(UTC) - timedelta(days=settings.mam_archive_after_days)
    return list(db.scalars(
        select(MediaAsset)
        .join(Project, Project.id == MediaAsset.project_id)
        .join(User, User.id == Project.owner_id)
        .where(
            Project.lifecycle_state == "completed",
            Project.last_accessed_at.is_not(None), Project.last_accessed_at < cutoff,
            User.subscription_tier == SubscriptionTier.PRO,
            MediaAsset.media_type == "video", MediaAsset.height >= 2160,
            MediaAsset.proxy_key.is_not(None), MediaAsset.raw_deleted_at.is_(None),
            MediaAsset.archive_status == "hot",
        )
        .with_for_update(skip_locked=True)
    ))


def create_hydration_job(db: Session, *, project: Project, requested_by: UUID, assets: Iterable[MediaAsset]) -> MediaHydrationJob | None:
    candidates = [asset for asset in assets if asset.raw_deleted_at is None and asset.archive_status in {"archived", "restore_requested"}]
    if not candidates:
        return None
    job = MediaHydrationJob(
        project_id=project.id, requested_by_id=requested_by, status="queued", progress=0,
        estimated_ready_at=datetime.now(UTC) + timedelta(hours=settings.mam_restore_eta_hours),
    )
    db.add(job)
    db.flush()
    for asset in candidates:
        asset.archive_status = "restore_requested"
        asset.restore_requested_at = datetime.now(UTC)
        db.add(MediaHydrationItem(hydration_job_id=job.id, media_asset_id=asset.id, status="queued"))
    return job


def render_assets_needing_hydration(db: Session, *, project_id: UUID, asset_ids: Iterable[UUID]) -> list[MediaAsset]:
    ids = list(set(asset_ids))
    if not ids:
        return []
    assets = list(db.scalars(select(MediaAsset).where(MediaAsset.project_id == project_id, MediaAsset.id.in_(ids))))
    if len(assets) != len(ids):
        raise ValueError("Render references an invalid project media asset")
    purged = [asset for asset in assets if asset.raw_deleted_at is not None]
    if purged:
        raise ValueError("One or more original files were deleted by the free-tier retention policy")
    return [asset for asset in assets if asset.archive_status in {"archived", "restore_requested"}]


def send_retention_notice(user: User, threshold_days: int) -> str | None:
    if not settings.sendgrid_api_key or not settings.sendgrid_from_email:
        raise RuntimeError("SendGrid is not configured")
    days_left = max(0, settings.mam_free_ttl_days - threshold_days)
    response = httpx.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {settings.sendgrid_api_key}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": user.email}]}],
            "from": {"email": settings.sendgrid_from_email},
            "subject": f"您的原始影片將於 {days_left} 天後刪除",
            "content": [{"type": "text/plain", "value": (
                f"您已 {threshold_days} 天未登入。免費方案的原始素材會在 {settings.mam_free_ttl_days} 天未登入後刪除；"
                f"低解析 Proxy 與 Timeline 會保留。請登入 {settings.web_app_base_url} 保留原始素材。"
            )}],
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.headers.get("x-message-id")


def notice_candidates(db: Session, *, threshold_days: int) -> list[User]:
    cutoff = datetime.now(UTC) - timedelta(days=threshold_days)
    return list(db.scalars(
        select(User).where(
            User.subscription_tier == SubscriptionTier.FREE,
            User.last_login_at.is_not(None), User.last_login_at < cutoff,
        )
    ))
