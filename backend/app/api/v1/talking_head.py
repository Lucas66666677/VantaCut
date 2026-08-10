from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline, User
from app.schemas.talking_head import TalkingHeadConfidenceRequest, TalkingHeadStatusResponse, TalkingHeadTaskResponse
from app.tasks.speaker_tasks import analyze_speaker_state

router = APIRouter(prefix="/timelines", tags=["talking-head-confidence"])


@router.post("/{timeline_id}/talking-head-confidence", response_model=TalkingHeadTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_talking_head_confidence(timeline_id: UUID, payload: TalkingHeadConfidenceRequest, db: Session = Depends(get_db)) -> TalkingHeadTaskResponse:
    timeline, user, asset = db.get(Timeline, timeline_id), db.get(User, payload.user_id), db.get(MediaAsset, payload.source_asset_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if asset is None or asset.project_id != timeline.project_id or asset.status != MediaStatus.READY or asset.media_type != MediaType.VIDEO: raise HTTPException(status_code=422, detail="source_asset_id must be a ready project video")
    if payload.enable_gaze_correction and payload.confirm_gaze_correction is not True: raise HTTPException(status_code=422, detail="confirm_gaze_correction=true is required before modifying eye direction")
    settings = dict(timeline.settings_json or {}); settings["talking_head_confidence"] = {"status": "queued", "markers": [], "advisory_only": True, "gaze_correction": {"status": "queued", "source_asset_id": str(asset.id)} if payload.enable_gaze_correction else None}; timeline.settings_json = settings; db.commit()
    task = analyze_speaker_state.delay(str(asset.id), str(timeline.id), payload.confidence_threshold, payload.enable_gaze_correction, payload.use_proxy_for_gaze); base = f"/api/v1/projects/{timeline.project_id}/status"
    return TalkingHeadTaskResponse(task_id=task.id, project_id=timeline.project_id, status="queued", status_sse_path=base, status_websocket_path=f"{base}/ws")


@router.get("/{timeline_id}/talking-head-confidence", response_model=TalkingHeadStatusResponse)
def talking_head_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> TalkingHeadStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("talking_head_confidence", {}))
    return TalkingHeadStatusResponse(status=str(record.get("status", "idle")), markers=list(record.get("markers", [])), gaze_correction=record.get("gaze_correction"), error=record.get("error"))
