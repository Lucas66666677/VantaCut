from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import MediaAsset, MediaStatus, User
from app.schemas.matting import VideoMattingRequest, VideoMattingTaskResponse
from app.tasks.matting_tasks import generate_video_matte


router = APIRouter(prefix="/media", tags=["video-matting"])


@router.post("/{media_asset_id}/matting", response_model=VideoMattingTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_video_matting(
    media_asset_id: UUID, payload: VideoMattingRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> VideoMattingTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot create a matte for this asset")
    if asset.status != MediaStatus.READY:
        raise HTTPException(status_code=409, detail="Media asset is not ready for matting")
    if asset.duration_seconds is not None and payload.frame_time > float(asset.duration_seconds):
        raise HTTPException(status_code=422, detail="frame_time lies outside the source video")
    task = generate_video_matte.delay(str(asset.id), payload.model_dump(mode="json"))
    project_path = f"/api/v1/projects/{asset.project_id}/status"
    return VideoMattingTaskResponse(
        task_id=task.id, media_asset_id=asset.id, project_id=asset.project_id, status="queued",
        status_sse_path=project_path, status_websocket_path=f"{project_path}/ws",
    )
