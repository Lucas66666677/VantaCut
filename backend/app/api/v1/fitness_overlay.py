from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline, User
from app.schemas.fitness_overlay import FitnessOverlayRequest, FitnessOverlayStatusResponse, FitnessOverlayTaskResponse
from app.tasks.fitness_overlay_tasks import analyze_fitness_reps

router = APIRouter(prefix="/timelines", tags=["fitness-overlay"])


@router.post("/{timeline_id}/fitness-overlay", response_model=FitnessOverlayTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_fitness_overlay(timeline_id: UUID, payload: FitnessOverlayRequest, db: Session = Depends(get_db)) -> FitnessOverlayTaskResponse:
    timeline, user, source = db.get(Timeline, timeline_id), db.get(User, payload.user_id), db.get(MediaAsset, payload.source_asset_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY or source.media_type != MediaType.VIDEO: raise HTTPException(status_code=422, detail="source_asset_id must be a ready project video")
    task = analyze_fitness_reps.delay(str(timeline.id), payload.model_dump(mode="json", exclude={"user_id"})); base = f"/api/v1/projects/{timeline.project_id}/status"
    return FitnessOverlayTaskResponse(task_id=task.id, project_id=timeline.project_id, status="queued", status_sse_path=base, status_websocket_path=f"{base}/ws")


@router.get("/{timeline_id}/fitness-overlay", response_model=FitnessOverlayStatusResponse)
def fitness_overlay_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> FitnessOverlayStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("fitness_overlay", {})); events = list(record.get("events", []))
    return FitnessOverlayStatusResponse(status=str(record.get("status", "idle")), rep_count=len(events), events=events, fatigue_event=record.get("fatigue_event"), error=record.get("error"))
