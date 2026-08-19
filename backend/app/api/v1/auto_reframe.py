from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Timeline, User
from app.services.non_destructive import append_filter_layer
from app.schemas.auto_reframe import AutoReframeRequest, AutoReframeResponse

router = APIRouter(prefix="/timelines", tags=["auto-reframe"])


@router.post("/{timeline_id}/auto-reframe", response_model=AutoReframeResponse)
def configure_auto_reframe(
    timeline_id: UUID, payload: AutoReframeRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoReframeResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    options = payload.model_dump(mode="json")
    options["enabled"] = True
    settings = {**dict(timeline.settings_json or {}), "auto_reframe": options}
    timeline.settings_json = append_filter_layer(
        settings,
        kind="auto_reframe",
        target={"timeline_id": str(timeline.id)},
        parameters=options,
        source="user",
    )
    db.commit()
    return AutoReframeResponse(timeline_id=timeline.id, status="configured", settings=options)
