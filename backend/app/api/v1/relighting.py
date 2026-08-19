from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.relighting import (
    RelightingAnalysisRequest,
    RelightingTaskResponse,
    VirtualRelightTimelineRequest,
    VirtualRelightTimelineResponse,
)
from app.services.virtual_relighting import VirtualLight, VirtualRelightSettings
from app.tasks.relighting_tasks import analyze_depth_and_lighting


router = APIRouter(tags=["virtual-relighting"])


@router.post("/media/{media_asset_id}/analyze-virtual-relight", response_model=RelightingTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_relighting_analysis(
    media_asset_id: UUID, payload: RelightingAnalysisRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> RelightingTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot analyze this media asset")
    task = analyze_depth_and_lighting.delay(str(asset.id), payload.depth_model, payload.frame_stride, payload.use_proxy)
    return RelightingTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.put("/timelines/{timeline_id}/virtual-relight", response_model=VirtualRelightTimelineResponse)
def update_virtual_relight(
    timeline_id: UUID, payload: VirtualRelightTimelineRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> VirtualRelightTimelineResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    settings = VirtualRelightSettings(
        enabled=payload.enabled,
        depth_model=payload.depth_model,
        temporal_depth_smoothing=payload.temporal_depth_smoothing,
        ambient_strength=payload.ambient_strength,
        lights=tuple(VirtualLight(**light.model_dump()) for light in payload.lights),
    )
    settings.validate()
    timeline.settings_json = {**dict(timeline.settings_json or {}), "virtual_relight": settings.to_dict()}
    db.commit()
    return VirtualRelightTimelineResponse(timeline_id=timeline.id, status="configured", settings=settings.to_dict())
