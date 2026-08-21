from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.screen_focus import ScreenFocusRequest, ScreenFocusTaskResponse
from app.tasks.screen_focus_tasks import analyze_timeline_screen_focus


router = APIRouter(prefix="/timelines", tags=["screen-focus"])


@router.post("/{timeline_id}/analyze-screen-focus", response_model=ScreenFocusTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_screen_focus_analysis(
    timeline_id: UUID, payload: ScreenFocusRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> ScreenFocusTaskResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot analyze this timeline")
    task = analyze_timeline_screen_focus.delay(str(timeline.id), payload.use_proxy, payload.sample_seconds)
    return ScreenFocusTaskResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
