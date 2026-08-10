from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.speed_curves import TimelineSpeedCurveUpdateRequest, TimelineSpeedCurveUpdateResponse


router = APIRouter(prefix="/timelines", tags=["speed-curves"])


@router.put("/{timeline_id}/speed-curves", response_model=TimelineSpeedCurveUpdateResponse)
def update_speed_curves(timeline_id: UUID, payload: TimelineSpeedCurveUpdateRequest, db: Session = Depends(get_db)) -> TimelineSpeedCurveUpdateResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    # Inspector saves one clip at a time; merge so editing Clip B never erases Clip A's curve.
    existing = dict(dict(timeline.settings_json or {}).get("speed_curves", {}))
    merged = {str(item.get("clip_id")): dict(item) for item in list(existing.get("curves", [])) if isinstance(item, dict)}
    for curve in payload.curves:
        serialized = curve.model_dump(mode="json")
        merged[str(serialized["clip_id"])] = serialized
    curves = list(merged.values())
    timeline.settings_json = {**dict(timeline.settings_json or {}), "speed_curves": {"version": 1, "curves": curves}}
    db.commit()
    return TimelineSpeedCurveUpdateResponse(timeline_id=timeline.id, status="configured", curves=curves)
