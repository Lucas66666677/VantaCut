from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.transitions import TimelineTransitionsRequest, TransitionBuildResponse
from app.tasks.transition_tasks import build_transition_asset


router = APIRouter(prefix="/timelines", tags=["transitions"])


def _owned(timeline: Timeline, user: User | None) -> None:
    if user is None or timeline.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")


@router.put("/{timeline_id}/transitions")
def update_transitions(timeline_id: UUID, payload: TimelineTransitionsRequest, db: Session = Depends(get_db)) -> dict:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    _owned(timeline, db.get(User, payload.user_id))
    ids = [item.id for item in payload.transitions]
    if len(ids) != len(set(ids)): raise HTTPException(status_code=422, detail="Transition IDs must be unique")
    graph = {"version": 1, "transitions": [item.model_dump(mode="json") for item in payload.transitions]}
    timeline.settings_json = {**dict(timeline.settings_json or {}), "transition_graph": graph}; db.commit()
    return {"timeline_id": str(timeline.id), "status": "configured", **graph}


@router.post("/{timeline_id}/transitions/{transition_id}/build", response_model=TransitionBuildResponse, status_code=status.HTTP_202_ACCEPTED)
def request_transition_asset(timeline_id: UUID, transition_id: str, user_id: UUID, db: Session = Depends(get_db)) -> TransitionBuildResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    _owned(timeline, db.get(User, user_id))
    specs = dict(timeline.settings_json.get("transition_graph", {})).get("transitions", [])
    if not any(item.get("id") == transition_id for item in specs): raise HTTPException(status_code=404, detail="Transition not found")
    task = build_transition_asset.delay(str(timeline.id), transition_id)
    return TransitionBuildResponse(task_id=task.id, timeline_id=timeline.id, transition_id=transition_id, status="queued")
