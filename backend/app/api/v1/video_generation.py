from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, Timeline, User
from app.schemas.video_generation import BRollGenerationRequest, VideoGenerationTaskResponse, VideoOutpaintRequest
from app.tasks.video_generation_tasks import generate_broll, outpaint_video


broll_router = APIRouter(prefix="/timelines", tags=["video-generation"])
outpaint_router = APIRouter(prefix="/video", tags=["video-outpaint"])


def _task_response(task_id: str, project_id: UUID) -> VideoGenerationTaskResponse:
    path = f"/api/v1/projects/{project_id}/status"
    return VideoGenerationTaskResponse(task_id=task_id, project_id=project_id, status="queued", status_sse_path=path, status_websocket_path=f"{path}/ws")


@broll_router.post("/{timeline_id}/b-roll/generate", response_model=VideoGenerationTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_broll_generation(timeline_id: UUID, payload: BRollGenerationRequest, db: Session = Depends(get_db)) -> VideoGenerationTaskResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot generate B-Roll for this timeline")
    source = db.get(MediaAsset, payload.source_asset_id)
    if source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY: raise HTTPException(status_code=422, detail="source_asset_id must be a ready project media asset")
    task = generate_broll.delay(str(timeline.id), payload.model_dump(mode="json", exclude={"user_id"}))
    return _task_response(task.id, timeline.project_id)


@outpaint_router.post("/outpaint", response_model=VideoGenerationTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_video_outpaint(payload: VideoOutpaintRequest, db: Session = Depends(get_db)) -> VideoGenerationTaskResponse:
    asset, user = db.get(MediaAsset, payload.media_asset_id), db.get(User, payload.user_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    if user is None or asset.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot outpaint this media asset")
    if asset.status != MediaStatus.READY: raise HTTPException(status_code=409, detail="Media asset is not ready")
    if asset.duration_seconds is not None and payload.end_time is not None and payload.end_time > float(asset.duration_seconds): raise HTTPException(status_code=422, detail="end_time lies outside the source video")
    task = outpaint_video.delay(str(asset.id), payload.model_dump(mode="json", exclude={"user_id", "media_asset_id"}))
    return _task_response(task.id, asset.project_id)
