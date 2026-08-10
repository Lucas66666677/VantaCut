from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.semantic_snapping import SemanticSnapPointsResponse
from app.services.semantic_snapping import build_semantic_snap_points

router = APIRouter(prefix="/timelines", tags=["semantic-snapping"])


@router.get("/{timeline_id}/semantic-snap-points", response_model=SemanticSnapPointsResponse)
def semantic_snap_points(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> SemanticSnapPointsResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this timeline")
    return SemanticSnapPointsResponse(timeline_id=timeline.id, points=build_semantic_snap_points(db, timeline))
