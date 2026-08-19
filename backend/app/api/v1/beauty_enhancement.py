from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.beauty_enhancement import BeautyEnhancementRequest, BeautyEnhancementResponse
from app.services.beauty_enhancement import BeautyEnhancement
from app.services.non_destructive import append_filter_layer


router = APIRouter(prefix="/timelines", tags=["beauty-enhancement"])


@router.put("/{timeline_id}/beauty-enhancement", response_model=BeautyEnhancementResponse)
def configure_beauty_enhancement(
    timeline_id: UUID,
    payload: BeautyEnhancementRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BeautyEnhancementResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")

    settings = BeautyEnhancement(**payload.model_dump())
    timeline.settings_json = append_filter_layer(
        {**dict(timeline.settings_json or {}), "beauty_enhancement": settings.as_json()},
        kind="beauty_enhancement", target={"scope": "timeline"}, parameters=settings.as_json(), source="user",
    )
    db.commit()
    return BeautyEnhancementResponse(
        timeline_id=timeline.id,
        status="configured",
        beauty_enhancement=settings.as_json(),
    )
