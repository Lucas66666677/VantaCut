from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import TemplateLicense, TemplateLicenseStatus, RenderJob, RenderStatus
from app.services.marketplace_settlement import settle_successful_render
from app.worker import celery_app


@celery_app.task(
    bind=True, name="marketplace.settle_successful_render", autoretry_for=(Exception,),
    retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 8},
)
def settle_template_license_after_render(self, render_job_id: str) -> dict[str, object]:
    """The render task invokes this only after committing COMPLETED; failed renders never transfer funds."""
    db = SessionLocal()
    try:
        settled = settle_successful_render(db, render_job_id=UUID(render_job_id))
        db.commit()
        return {"render_job_id": render_job_id, "settled": settled}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name="marketplace.reconcile_pending_settlements")
def reconcile_pending_settlements() -> dict[str, int]:
    """Outbox-style safety net when the render worker could not enqueue a settlement task."""
    db = SessionLocal()
    try:
        render_ids = db.scalars(
            select(TemplateLicense.render_job_id)
            .join(RenderJob, RenderJob.id == TemplateLicense.render_job_id)
            .where(
                TemplateLicense.status == TemplateLicenseStatus.RENDERING.value,
                RenderJob.status == RenderStatus.COMPLETED,
                TemplateLicense.render_job_id.is_not(None),
            )
        ).all()
    finally:
        db.close()
    queued = 0
    for render_id in render_ids:
        settle_template_license_after_render.delay(str(render_id))
        queued += 1
    return {"queued": queued}
