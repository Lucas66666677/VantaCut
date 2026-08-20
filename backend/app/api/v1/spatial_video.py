from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import RenderJob, RenderStatus, SpatialVideoJob, Timeline, User
from app.schemas.spatial_video import SpatialVideoExportRequest, SpatialVideoExportResponse
from app.tasks.spatial_video_tasks import render_mvhevc_spatial_video


router = APIRouter(prefix="/timelines", tags=["spatial-video"])


@router.post("/{timeline_id}/spatial-video", response_model=SpatialVideoExportResponse, status_code=status.HTTP_202_ACCEPTED)
def request_spatial_video_export(
    timeline_id: UUID, payload: SpatialVideoExportRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> SpatialVideoExportResponse:
    timeline = db.get(Timeline, timeline_id)
    source = db.get(RenderJob, payload.source_render_job_id)
    if timeline is None or source is None:
        raise HTTPException(status_code=404, detail="Timeline or source render job not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot export this timeline")
    if source.timeline_id != timeline.id or source.status != RenderStatus.COMPLETED or not source.output_key:
        raise HTTPException(status_code=422, detail="source_render_job_id must be a completed render for this timeline")
    job = SpatialVideoJob(
        project_id=timeline.project_id, timeline_id=timeline.id, source_render_job_id=source.id,
        options_json=payload.model_dump(mode="json", exclude={"source_render_job_id"}),
    )
    db.add(job); db.commit(); db.refresh(job)
    try:
        task = render_mvhevc_spatial_video.delay(str(job.id))
    except Exception as exc:
        job.status, job.error_message = "failed", f"Unable to enqueue spatial video worker: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail="Spatial video worker queue is unavailable") from exc
    return SpatialVideoExportResponse(spatial_video_job_id=job.id, task_id=task.id, status=job.status)
