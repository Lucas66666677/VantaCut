from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.keyframes import TimelineKeyframeUpdateRequest, TimelineKeyframeUpdateResponse


router = APIRouter(prefix="/timelines", tags=["keyframes"])


@router.put("/{timeline_id}/keyframes", response_model=TimelineKeyframeUpdateResponse)
def update_timeline_keyframes(
    timeline_id: UUID, payload: TimelineKeyframeUpdateRequest, db: Session = Depends(get_db),
) -> TimelineKeyframeUpdateResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    animations = [item.model_dump(mode="json") for item in payload.animations]
    timeline.settings_json = {**dict(timeline.settings_json or {}), "motion_keyframes": {"version": 1, "animations": animations}}
    db.commit()
    return TimelineKeyframeUpdateResponse(timeline_id=timeline.id, status="configured", animations=animations)
