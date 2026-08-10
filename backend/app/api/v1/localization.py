from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User, VoiceProfile, VoiceProfileStatus
from app.schemas.localization import LocalizedDubRequest, LocalizedDubResponse
from app.tasks.localization_tasks import generate_dubbed_version

router = APIRouter(prefix="/timelines", tags=["localization"])

@router.post("/{timeline_id}/localized-dubs", response_model=LocalizedDubResponse, status_code=status.HTTP_202_ACCEPTED)
def request_localized_dub(timeline_id: UUID, payload: LocalizedDubRequest, db: Session = Depends(get_db)) -> LocalizedDubResponse:
    timeline, user, profile = db.get(Timeline, timeline_id), db.get(User, payload.user_id), db.get(VoiceProfile, payload.voice_profile_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot localize this timeline")
    if profile is None or profile.project_id != timeline.project_id or profile.status != VoiceProfileStatus.READY: raise HTTPException(status_code=422, detail="A ready project voice profile is required")
    task = generate_dubbed_version.delay(str(timeline.id), payload.model_dump(mode="json"))
    return LocalizedDubResponse(task_id=task.id, timeline_id=timeline.id, target_language=payload.target_language, status="queued")
