from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.text_to_music import GenerateMusicRequest, GenerateMusicResponse, GenerateMusicStatusResponse
from app.services.text_to_music import timeline_duration_seconds
from app.tasks.text_to_music_tasks import generate_timeline_music


router = APIRouter(prefix="/timelines", tags=["text-to-music"])


def _timeline_for_user(db: Session, timeline_id: UUID, user_id: UUID) -> Timeline:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    return timeline


@router.post("/{timeline_id}/generated-music", response_model=GenerateMusicResponse, status_code=status.HTTP_202_ACCEPTED)
def request_generated_music(timeline_id: UUID, payload: GenerateMusicRequest, db: Session = Depends(get_db)) -> GenerateMusicResponse:
    timeline = _timeline_for_user(db, timeline_id, payload.user_id)
    settings, document = dict(timeline.settings_json or {}), dict((timeline.settings_json or {}).get("confirmed_timeline", {}))
    duration = timeline_duration_seconds(document)
    if duration <= 0:
        raise HTTPException(status_code=409, detail="Confirm a non-empty timeline before generating BGM")
    request = payload.model_dump(mode="json")
    settings["generated_music"] = {"status": "queued", **request, "target_duration_seconds": duration}
    timeline.settings_json = settings; db.commit()
    task = generate_timeline_music.delay(str(timeline.id), request, duration)
    return GenerateMusicResponse(task_id=task.id, timeline_id=timeline.id, status="queued", target_duration_seconds=duration)


@router.get("/{timeline_id}/generated-music", response_model=GenerateMusicStatusResponse)
def generated_music_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> GenerateMusicStatusResponse:
    timeline = _timeline_for_user(db, timeline_id, user_id)
    record = dict(dict(timeline.settings_json or {}).get("generated_music", {}))
    return GenerateMusicStatusResponse(**record)
