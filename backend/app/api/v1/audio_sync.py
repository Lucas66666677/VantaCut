from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline, User
from app.schemas.audio_sync import AudioSyncRequest, AudioSyncStatusResponse, AudioSyncTaskResponse
from app.tasks.audio_sync_tasks import align_external_audio

router = APIRouter(prefix="/timelines", tags=["audio-sync"])


@router.post("/{timeline_id}/audio-sync", response_model=AudioSyncTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_audio_sync(timeline_id: UUID, payload: AudioSyncRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AudioSyncTaskResponse:
    timeline = db.get(Timeline, timeline_id)
    video, external = db.get(MediaAsset, payload.video_asset_id), db.get(MediaAsset, payload.external_audio_asset_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if video is None or external is None or video.project_id != timeline.project_id or external.project_id != timeline.project_id or video.status != MediaStatus.READY or external.status != MediaStatus.READY or video.media_type != MediaType.VIDEO or external.media_type != MediaType.AUDIO: raise HTTPException(status_code=422, detail="Select a ready project video and ready external audio asset")
    task = align_external_audio.delay(str(timeline.id), payload.model_dump(mode="json")); base = f"/api/v1/projects/{timeline.project_id}/status"
    return AudioSyncTaskResponse(task_id=task.id, project_id=timeline.project_id, status="queued", status_sse_path=base, status_websocket_path=f"{base}/ws")


@router.get("/{timeline_id}/audio-sync", response_model=AudioSyncStatusResponse)
def audio_sync_status(timeline_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AudioSyncStatusResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("audio_sync", {}))
    return AudioSyncStatusResponse(status=str(record.get("status", "idle")), offset_seconds=record.get("offset_seconds"), confidence=record.get("confidence"), audio_clip=record.get("audio_clip"), error=record.get("error"))
