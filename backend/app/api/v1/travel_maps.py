from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline, User
from app.schemas.travel_maps import TravelMapRequest, TravelMapStatusResponse, TravelMapTaskResponse
from app.tasks.travel_map_tasks import generate_travel_map


router = APIRouter(prefix="/timelines", tags=["travel-maps"])


@router.post("/{timeline_id}/travel-map", response_model=TravelMapTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_travel_map(timeline_id: UUID, payload: TravelMapRequest, db: Session = Depends(get_db)) -> TravelMapTaskResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if payload.source_asset_id:
        asset = db.get(MediaAsset, payload.source_asset_id)
        if asset is None or asset.project_id != timeline.project_id or asset.status != MediaStatus.READY or asset.media_type != MediaType.VIDEO:
            raise HTTPException(status_code=422, detail="source_asset_id must be a ready project video")
    if not payload.route_text and not payload.source_asset_id:
        raise HTTPException(status_code=422, detail="Provide route_text or a source asset with a timed transcript")
    task = generate_travel_map.delay(str(timeline.id), payload.model_dump(mode="json", exclude={"user_id"}))
    base = f"/api/v1/projects/{timeline.project_id}/status"
    return TravelMapTaskResponse(task_id=task.id, project_id=timeline.project_id, status="queued", status_sse_path=base, status_websocket_path=f"{base}/ws")


@router.get("/{timeline_id}/travel-map", response_model=TravelMapStatusResponse)
def travel_map_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> TravelMapStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("travel_map", {}))
    return TravelMapStatusResponse(status=str(record.get("status", "idle")), clip=record.get("clip"), route=list(record.get("route", [])), error=record.get("error"))
