from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.audio_description import AudioDescriptionResponse, GenerateAudioDescriptionRequest
from app.tasks.audio_description_tasks import generate_audio_description

router = APIRouter(prefix="/timelines", tags=["accessibility"])


def _timeline_not_found() -> HTTPException:
    # Same response whether the timeline truly doesn't exist or exists but
    # belongs to someone else's project — do not confirm the existence of
    # another user's private timeline to an unauthorized caller.
    return HTTPException(status_code=404, detail="Timeline not found")


def _authorize_timeline_owner(db: Session, timeline_id: UUID, current_user: User) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None or timeline.project.owner_id != current_user.id:
        raise _timeline_not_found()
    return timeline


@router.post("/{timeline_id}/generate-audio-description", response_model=AudioDescriptionResponse, status_code=status.HTTP_202_ACCEPTED)
def request_audio_description(
    timeline_id: UUID,
    payload: GenerateAudioDescriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AudioDescriptionResponse:
    # Ownership is verified BEFORE any other resolution, mutation, or
    # enqueue below — an unauthorized (anonymous, invalid-token, or
    # non-owner) caller gets a plain 404 and generate_audio_description is
    # never scheduled, and no db.commit() ever runs on their behalf.
    timeline = _authorize_timeline_owner(db, timeline_id, current_user)
    asset = db.get(MediaAsset, payload.source_asset_id)
    if asset is None or asset.project_id != timeline.project_id or not (asset.proxy_key or asset.storage_key):
        raise HTTPException(status_code=400, detail="Source asset does not belong to this project or has no preview video")
    confirmed = dict(timeline.settings_json.get("confirmed_timeline", {}))
    if str(confirmed.get("source_asset_id", "")) != str(asset.id):
        raise HTTPException(status_code=422, detail="Audio description requires the current confirmed timeline for this source asset")
    timeline.settings_json = {**dict(timeline.settings_json or {}), "audio_description": {"status": "queued", "language": payload.language, "min_gap_seconds": payload.min_gap_seconds, "mode": payload.mode}}
    db.commit()
    task = generate_audio_description.delay(str(timeline.id))
    return AudioDescriptionResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
