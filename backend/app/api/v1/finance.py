import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.finance import FinanceAnnotationsUpdate, FinanceTrackRequest, FinanceTrackResponse
from app.tasks.finance_tasks import refresh_finance_track


router = APIRouter(prefix="/timelines", tags=["finance"])


def _authorised_timeline(timeline_id: UUID, current_user: User, db: Session) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot manage finance tracks on this timeline")
    return timeline


@router.post("/{timeline_id}/finance-tracks", response_model=FinanceTrackResponse, status_code=status.HTTP_202_ACCEPTED)
def create_finance_track(timeline_id: UUID, payload: FinanceTrackRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FinanceTrackResponse:
    timeline = _authorised_timeline(timeline_id, current_user, db)
    finance_track_id = str(uuid.uuid4())
    track = {"id": finance_track_id, "status": "processing", **payload.model_dump(mode="json")}
    settings = dict(timeline.settings_json or {})
    timeline.settings_json = {**settings, "finance_tracks": [*list(settings.get("finance_tracks", [])), track]}
    db.commit()
    task = refresh_finance_track.delay(str(timeline.id), finance_track_id)
    return FinanceTrackResponse(finance_track_id=finance_track_id, task_id=task.id, timeline_id=timeline.id, status="queued")


@router.get("/{timeline_id}/finance-tracks")
def list_finance_tracks(timeline_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    timeline = _authorised_timeline(timeline_id, current_user, db)
    return {"timeline_id": str(timeline.id), "tracks": list(dict(timeline.settings_json or {}).get("finance_tracks", []))}


@router.patch("/{timeline_id}/finance-tracks/{finance_track_id}", response_model=FinanceTrackResponse, status_code=status.HTTP_202_ACCEPTED)
def update_finance_track_annotations(
    timeline_id: UUID,
    finance_track_id: str,
    payload: FinanceAnnotationsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FinanceTrackResponse:
    timeline = _authorised_timeline(timeline_id, current_user, db)
    settings = dict(timeline.settings_json or {})
    tracks = list(settings.get("finance_tracks", []))
    index = next((i for i, item in enumerate(tracks) if item.get("id") == finance_track_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Finance track not found")
    tracks[index] = {**dict(tracks[index]), "annotations": [item.model_dump(mode="json") for item in payload.annotations], "status": "processing", "error": None}
    timeline.settings_json = {**settings, "finance_tracks": tracks}
    db.commit()
    task = refresh_finance_track.delay(str(timeline.id), finance_track_id)
    return FinanceTrackResponse(finance_track_id=finance_track_id, task_id=task.id, timeline_id=timeline.id, status="queued")
