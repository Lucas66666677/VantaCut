from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, User
from app.schemas.inpainting import VideoInpaintingRequest, VideoInpaintingTaskResponse
from app.tasks.inpainting_tasks import inpaint_selected_object


router = APIRouter(prefix="/media", tags=["video-inpainting"])


@router.post("/{media_asset_id}/inpaint", response_model=VideoInpaintingTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_video_inpainting(
    media_asset_id: UUID,
    payload: VideoInpaintingRequest,
    db: Session = Depends(get_db),
) -> VideoInpaintingTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    user = db.get(User, payload.user_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if user is None or asset.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot inpaint this media asset")
    if asset.status != MediaStatus.READY:
        raise HTTPException(status_code=409, detail="Media asset is not ready for inpainting")
    if asset.duration_seconds is not None and payload.frame_time > float(asset.duration_seconds):
        raise HTTPException(status_code=422, detail="frame_time lies outside the source video")
    if asset.duration_seconds is not None and payload.end_time is not None and payload.end_time > float(asset.duration_seconds):
        raise HTTPException(status_code=422, detail="end_time lies outside the source video")

    task = inpaint_selected_object.delay(str(asset.id), payload.model_dump(mode="json", exclude={"user_id"}))
    project_path = f"/api/v1/projects/{asset.project_id}/status"
    return VideoInpaintingTaskResponse(
        task_id=task.id,
        media_asset_id=asset.id,
        project_id=asset.project_id,
        status="queued",
        status_sse_path=project_path,
        status_websocket_path=f"{project_path}/ws",
    )
