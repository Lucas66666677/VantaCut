from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import AvatarProfile, AvatarRenderJob, MediaAsset, Project, Timeline, User
from app.schemas.avatar import AvatarProfileCreate, AvatarProfileResponse, AvatarRenderRequest, AvatarRenderResponse
from app.tasks.avatar_tasks import render_avatar_replacement


router = APIRouter(prefix="/avatars", tags=["virtual-avatar"])


@router.post("/profiles", response_model=AvatarProfileResponse, status_code=status.HTTP_201_CREATED)
def create_avatar_profile(payload: AvatarProfileCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AvatarProfileResponse:
    if payload.project_id:
        timeline_project = db.get(Project, payload.project_id)
        if timeline_project is None or timeline_project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="Project does not belong to user")
    profile = AvatarProfile(owner_id=current_user.id, project_id=payload.project_id, name=payload.name, renderer=payload.renderer, asset_bundle_key=payload.asset_bundle_key, rig_mapping_json=payload.rig_mapping)
    db.add(profile); db.commit(); db.refresh(profile)
    return AvatarProfileResponse(id=profile.id, name=profile.name, renderer=profile.renderer, status=profile.status)


@router.post("/timelines/{timeline_id}/replace-segment", response_model=AvatarRenderResponse, status_code=status.HTTP_202_ACCEPTED)
def replace_timeline_segment(timeline_id: UUID, payload: AvatarRenderRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AvatarRenderResponse:
    timeline = db.get(Timeline, timeline_id)
    source, profile = db.get(MediaAsset, payload.source_asset_id), db.get(AvatarProfile, payload.avatar_profile_id)
    if timeline is None or source is None or profile is None: raise HTTPException(status_code=404, detail="Timeline, source asset, or avatar profile not found")
    if timeline.project.owner_id != current_user.id or source.project_id != timeline.project_id or profile.owner_id != current_user.id: raise HTTPException(status_code=403, detail="Avatar replacement is not authorised")
    if payload.source_end > float(source.duration_seconds or 0): raise HTTPException(status_code=422, detail="Avatar replacement exceeds source duration")
    job = AvatarRenderJob(project_id=timeline.project_id, timeline_id=timeline.id, avatar_profile_id=profile.id, source_asset_id=source.id, source_start=payload.source_start, source_end=payload.source_end, provenance_json={"subject_consent": True, "asset_license": "creator_confirmed", "disclosure": "digital_avatar"})
    db.add(job); db.commit(); db.refresh(job)
    task = render_avatar_replacement.delay(str(job.id))
    return AvatarRenderResponse(avatar_render_job_id=job.id, task_id=task.id, status=job.status)


@router.get("/render-jobs/{job_id}")
def avatar_render_status(job_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    job = db.get(AvatarRenderJob, job_id)
    if job is None: raise HTTPException(status_code=404, detail="Avatar render job not found")
    profile = db.get(AvatarProfile, job.avatar_profile_id)
    if profile is None or current_user.id != profile.owner_id:
        raise HTTPException(status_code=403, detail="User cannot inspect this avatar render")
    return {"id": str(job.id), "status": job.status, "progress": job.progress, "output_asset_id": str(job.output_asset_id) if job.output_asset_id else None, "error_message": job.error_message, "provenance": job.provenance_json}
