from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, User
from app.schemas.speaker import GazeRedirectionRequest, SpeakerStateRequest, SpeakerTaskResponse
from app.tasks.speaker_tasks import analyze_speaker_state, redirect_gaze


router = APIRouter(prefix="/media", tags=["speaker-state"])


def _authorise_ready_asset(db: Session, media_asset_id: UUID, current_user: User) -> MediaAsset:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot process this media asset")
    if asset.status != MediaStatus.READY:
        raise HTTPException(status_code=409, detail="Media asset is not ready for analysis")
    return asset


@router.post("/{media_asset_id}/analyze-speaker-state", response_model=SpeakerTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_speaker_state_analysis(
    media_asset_id: UUID,
    payload: SpeakerStateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpeakerTaskResponse:
    asset = _authorise_ready_asset(db, media_asset_id, current_user)
    task = analyze_speaker_state.delay(str(asset.id))
    return SpeakerTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.post("/{media_asset_id}/redirect-gaze", response_model=SpeakerTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_gaze_redirection(
    media_asset_id: UUID,
    payload: GazeRedirectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpeakerTaskResponse:
    """Create a reversible gaze-corrected preview after an explicit creator consent signal."""
    asset = _authorise_ready_asset(db, media_asset_id, current_user)
    task = redirect_gaze.delay(str(asset.id), payload.use_proxy)
    return SpeakerTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")
