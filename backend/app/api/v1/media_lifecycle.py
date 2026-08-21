from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaHydrationJob, Project, User
from app.schemas.mam import HydrateProjectRequest, HydrationResponse, ProjectStorageActor, ProjectStorageStatusResponse, ArchivedAssetStatus
from app.services.media_lifecycle import create_hydration_job
from app.tasks.mam_tasks import restore_hydration_job


router = APIRouter(prefix="/projects", tags=["media-lifecycle"])


def _project_for_user(db: Session, project_id: UUID, user_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _hydration_response(job: MediaHydrationJob | None) -> HydrationResponse | None:
    if job is None:
        return None
    return HydrationResponse(
        hydration_job_id=job.id, status=job.status, progress=job.progress,
        estimated_ready_at=job.estimated_ready_at,
        message="高畫質素材已可渲染" if job.status == "completed" else "正在從冷庫調回高畫質素材，預計需要 12 小時",
    )


@router.post("/{project_id}/storage/mark-completed", status_code=status.HTTP_204_NO_CONTENT)
def mark_project_completed(project_id: UUID, payload: ProjectStorageActor, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    project = _project_for_user(db, project_id, current_user.id)
    now = datetime.now(UTC)
    project.lifecycle_state, project.completed_at, project.last_accessed_at = "completed", now, now
    current_user.last_login_at = now
    db.commit()


@router.get("/{project_id}/storage/status", response_model=ProjectStorageStatusResponse)
def project_storage_status(project_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectStorageStatusResponse:
    project = _project_for_user(db, project_id, current_user.id)
    now = datetime.now(UTC)
    project.last_accessed_at, current_user.last_login_at = now, now
    assets = db.scalars(select(MediaAsset).where(MediaAsset.project_id == project.id)).all()
    job = db.scalar(select(MediaHydrationJob).where(
        MediaHydrationJob.project_id == project.id, MediaHydrationJob.status.in_(["queued", "restoring"])
    ).order_by(MediaHydrationJob.created_at.desc()))
    db.commit()
    raw_assets = [asset for asset in assets if asset.media_type.value in {"video", "audio"}]
    cold_or_purged = [asset for asset in raw_assets if asset.archive_status in {"archived", "restore_requested", "purged"}]
    return ProjectStorageStatusResponse(
        project_id=project.id, lifecycle_state=project.lifecycle_state,
        proxy_playback_available=all(asset.proxy_key for asset in assets if asset.media_type.value == "video"),
        high_quality_render_ready=not cold_or_purged,
        active_hydration=_hydration_response(job),
        assets=[ArchivedAssetStatus(
            asset_id=asset.id, filename=asset.filename, archive_status=asset.archive_status,
            proxy_available=bool(asset.proxy_key), restore_expires_at=asset.restore_expires_at,
        ) for asset in assets],
    )


@router.post("/{project_id}/storage/hydrate", response_model=HydrationResponse, status_code=status.HTTP_202_ACCEPTED)
def hydrate_project(project_id: UUID, payload: HydrateProjectRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> HydrationResponse:
    project = _project_for_user(db, project_id, current_user.id)
    query = select(MediaAsset).where(MediaAsset.project_id == project.id)
    if payload.media_asset_ids:
        query = query.where(MediaAsset.id.in_(payload.media_asset_ids))
    job = create_hydration_job(db, project=project, requested_by=current_user.id, assets=db.scalars(query).all())
    if job is None:
        raise HTTPException(status_code=409, detail="No archived raw assets require hydration")
    db.commit(); db.refresh(job)
    restore_hydration_job.delay(str(job.id))
    return _hydration_response(job)  # type: ignore[return-value]
