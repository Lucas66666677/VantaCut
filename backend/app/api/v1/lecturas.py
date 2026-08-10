import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import AvatarProfile, MediaAsset, Timeline, User
from app.schemas.lecturas import LecturasRequest, LecturasTaskResponse
from app.tasks.lecturas_tasks import generate_lecturas_interventions


router = APIRouter(prefix="/timelines", tags=["lecturas"])


@router.post("/{timeline_id}/lecturas", response_model=LecturasTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_lecturas(timeline_id: UUID, payload: LecturasRequest, db: Session = Depends(get_db)) -> LecturasTaskResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    asset, profile = db.get(MediaAsset, payload.source_asset_id), db.get(AvatarProfile, payload.avatar_profile_id)
    if timeline is None or user is None or asset is None or profile is None:
        raise HTTPException(status_code=404, detail="Timeline, user, source asset, or assistant avatar was not found")
    if not timeline.is_current:
        raise HTTPException(status_code=409, detail="Start Lecturas from the current Timeline version")
    if timeline.project.owner_id != user.id or asset.project_id != timeline.project_id or profile.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Lecturas is not authorised for this project or avatar")
    if profile.status != "ready":
        raise HTTPException(status_code=409, detail="Assistant avatar profile is not ready")
    run_id = str(uuid.uuid4()); settings = dict(timeline.settings_json or {})
    timeline.settings_json = {**settings, "lecturas_runs": [*list(settings.get("lecturas_runs", [])), {"run_id": run_id, "status": "queued", "assistant_name": payload.assistant_name}]}
    db.commit()
    task = generate_lecturas_interventions.delay(str(timeline.id), run_id, payload.model_dump(mode="json"))
    return LecturasTaskResponse(run_id=run_id, task_id=task.id, source_timeline_id=timeline.id, status="queued")
