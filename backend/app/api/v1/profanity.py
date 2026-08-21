from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.profanity import ProfanityFilterRequest, ProfanityFilterResponse
from app.tasks.profanity_tasks import apply_profanity_filter


router = APIRouter(prefix="/timelines", tags=["profanity-filter"])


@router.post("/{timeline_id}/profanity-filter", response_model=ProfanityFilterResponse, status_code=status.HTTP_202_ACCEPTED)
def request_profanity_filter(timeline_id: UUID, payload: ProfanityFilterRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProfanityFilterResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if dict(timeline.settings_json or {}).get("subtitles", {}).get("status") != "completed": raise HTTPException(status_code=409, detail="Generate timestamped subtitles before applying profanity filtering")
    timeline.settings_json = {**dict(timeline.settings_json or {}), "profanity_filter": {"status": "queued", "sfx_style": payload.sfx_style, "emoji_style": payload.emoji_style}}
    db.commit(); task = apply_profanity_filter.delay(str(timeline.id), payload.sfx_style, payload.emoji_style)
    return ProfanityFilterResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
