from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.academic import AcademicModeRequest, AcademicTaskResponse
from app.tasks.academic_tasks import assemble_academic_timeline


router = APIRouter(prefix="/timelines", tags=["academic-mode"])


@router.post("/{timeline_id}/academic-mode", response_model=AcademicTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def enable_academic_mode(timeline_id: UUID, payload: AcademicModeRequest, db: Session = Depends(get_db)) -> AcademicTaskResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if not timeline.is_current:
        raise HTTPException(status_code=409, detail="Start academic assembly from the current Timeline version")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot enable academic mode on this timeline")
    settings = dict(timeline.settings_json or {})
    # This source-level configuration lets later rough-cut/ASR jobs protect terminology too.
    timeline.settings_json = {**settings, "academic_glossary": [item.model_dump(mode="json") for item in payload.glossary], "academic_mode": {"status": "queued", "template_id": "research_pitch_v1"}}
    db.commit(); task = assemble_academic_timeline.delay(str(timeline.id), payload.model_dump(mode="json"))
    return AcademicTaskResponse(task_id=task.id, source_timeline_id=timeline.id, status="queued")
