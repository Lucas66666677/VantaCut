from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from botocore.exceptions import ClientError
from sqlalchemy import select

from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaHydrationItem, MediaHydrationJob, Project, StorageRetentionNotice, SubscriptionTier, User
from app.services.media_lifecycle import (
    ARCHIVED_CLASSES, archive_candidates, is_restore_complete, is_restore_in_progress, notice_candidates,
    parse_restore_expiry, send_retention_notice,
)
from app.services.storage import configure_raw_archive_lifecycle, delete_object, initiate_deep_archive_restore, object_archive_info, tag_for_deep_archive
from app.worker import celery_app


@celery_app.task(name="mam.configure_lifecycle")
def configure_lifecycle() -> dict[str, bool]:
    configure_raw_archive_lifecycle()
    return {"configured": settings.mam_s3_lifecycle_enabled}


@celery_app.task(name="mam.archive_completed_projects")
def archive_completed_projects() -> dict[str, int]:
    if not settings.mam_s3_lifecycle_enabled:
        return {"tagged": 0}
    db = SessionLocal(); tagged = 0
    try:
        for asset in archive_candidates(db):
            tag_for_deep_archive(asset.storage_key)
            asset.archive_status, asset.archive_requested_at = "archive_queued", datetime.now(UTC)
            tagged += 1
        db.commit()
        return {"tagged": tagged}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


@celery_app.task(name="mam.refresh_archive_and_hydration")
def refresh_archive_and_hydration() -> dict[str, int]:
    db = SessionLocal(); archived = restored = failed = 0
    try:
        assets = db.scalars(select(MediaAsset).where(MediaAsset.archive_status.in_(["archive_queued", "restore_requested", "restored"]))).all()
        for asset in assets:
            try:
                info = object_archive_info(asset.storage_key)
                storage_class, restore_header = str(info["storage_class"]), info.get("restore")
                if asset.archive_status == "archive_queued" and storage_class in ARCHIVED_CLASSES:
                    asset.archive_status, asset.archived_at = "archived", datetime.now(UTC); archived += 1
                elif asset.archive_status == "restore_requested" and is_restore_complete(str(restore_header) if restore_header else None):
                    asset.archive_status, asset.restore_expires_at = "restored", parse_restore_expiry(str(restore_header) if restore_header else None); restored += 1
                    for item in asset.hydration_job_items:
                        if item.status != "restored":
                            item.status, item.restore_header = "restored", str(restore_header) if restore_header else None
                elif asset.archive_status == "restored" and storage_class in ARCHIVED_CLASSES and not is_restore_complete(str(restore_header) if restore_header else None):
                    # A Glacier restore is a temporary standard-storage copy; once it expires the
                    # object remains Deep Archive and the next HQ export must request hydration again.
                    asset.archive_status, asset.restore_expires_at = "archived", None
            except ClientError:
                failed += 1
        jobs = db.scalars(select(MediaHydrationJob).where(MediaHydrationJob.status.in_(["queued", "restoring"]))).all()
        for job in jobs:
            items = job.items
            total = max(1, len(items)); ready = sum(item.status == "restored" for item in items); errors = sum(item.status == "failed" for item in items)
            job.progress = int(100 * ready / total)
            job.status = "completed" if ready == total else ("failed" if errors == total else "restoring")
            publish_project_status(str(job.project_id), progress=job.progress, stage="cold_storage_hydration", status="completed" if job.status == "completed" else "processing", message="高畫質素材已可渲染" if job.status == "completed" else "正在從冷庫調回高畫質素材，預計需要 12 小時", job_id=str(job.id))
        db.commit()
        return {"archived": archived, "restored": restored, "errors": failed}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


@celery_app.task(name="mam.restore_hydration_job")
def restore_hydration_job(hydration_job_id: str) -> dict[str, int]:
    db = SessionLocal(); requested = 0
    try:
        job = db.get(MediaHydrationJob, UUID(hydration_job_id))
        if not job: raise ValueError("Hydration job not found")
        job.status, job.progress = "restoring", 5
        for item in job.items:
            asset = item.media_asset
            try:
                initiate_deep_archive_restore(asset.storage_key)
                item.status = "restore_requested"; requested += 1
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"RestoreAlreadyInProgress", "ObjectAlreadyInActiveTierError"}:
                    item.status = "restore_requested"
                else:
                    item.status, item.error_message = "failed", str(exc)
        db.commit()
        publish_project_status(str(job.project_id), progress=5, stage="cold_storage_hydration", message="正在從冷庫調回高畫質素材，預計需要 12 小時", job_id=str(job.id), extra={"estimated_ready_at": job.estimated_ready_at.isoformat() if job.estimated_ready_at else None})
        return {"requested": requested}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


@celery_app.task(name="mam.send_free_tier_retention_notices")
def send_free_tier_retention_notices() -> dict[str, int]:
    db = SessionLocal(); sent = failed = 0
    try:
        for threshold in settings.mam_notice_days:
            for user in notice_candidates(db, threshold_days=threshold):
                notice = db.scalar(select(StorageRetentionNotice).where(
                    StorageRetentionNotice.user_id == user.id,
                    StorageRetentionNotice.inactive_day_threshold == threshold,
                ).with_for_update())
                if notice and notice.status == "sent":
                    continue
                if notice is None:
                    notice = StorageRetentionNotice(user_id=user.id, inactive_day_threshold=threshold, status="sending")
                    db.add(notice); db.flush()
                else:
                    notice.status, notice.error_message = "sending", None
                try:
                    notice.provider_message_id = send_retention_notice(user, threshold)
                    notice.status, notice.sent_at = "sent", datetime.now(UTC); sent += 1
                except Exception as exc:
                    notice.status, notice.error_message = "failed", str(exc); failed += 1
        db.commit(); return {"sent": sent, "failed": failed}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


@celery_app.task(name="mam.purge_inactive_free_raw_assets")
def purge_inactive_free_raw_assets() -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=settings.mam_free_ttl_days)
    db = SessionLocal(); purged = 0
    try:
        assets = db.scalars(select(MediaAsset).join(Project).join(User).where(
            User.subscription_tier == SubscriptionTier.FREE, User.last_login_at.is_not(None), User.last_login_at < cutoff,
            MediaAsset.raw_deleted_at.is_(None), MediaAsset.media_type.in_(["video", "audio"]),
        ).with_for_update(skip_locked=True)).all()
        for asset in assets:
            delete_object(asset.storage_key)
            asset.raw_deleted_at, asset.archive_status = datetime.now(UTC), "purged"; purged += 1
        db.commit(); return {"purged": purged}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
