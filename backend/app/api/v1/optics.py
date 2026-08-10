from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, User
from app.schemas.optics import OpticalFlowRetimeRequest, OpticalLookRequest, OpticalTaskResponse, OpticsAnalysisRequest
from app.tasks.optical_tasks import analyze_optics, render_optical_look_preview, retime_with_optical_flow


router = APIRouter(prefix="/media", tags=["optics"])


def _authorise(db: Session, asset_id: UUID, user_id: UUID) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    user = db.get(User, user_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if user is None or asset.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot process this media asset")
    return asset


@router.post("/{media_asset_id}/analyze-optics", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optics_analysis(media_asset_id: UUID, payload: OpticsAnalysisRequest, db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, payload.user_id)
    task = analyze_optics.delay(str(asset.id))
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.post("/{media_asset_id}/retime-optical-flow", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optical_flow_retime(media_asset_id: UUID, payload: OpticalFlowRetimeRequest, db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, payload.user_id)
    task = retime_with_optical_flow.delay(str(asset.id), payload.slow_motion_factor, payload.apply_motion_blur, payload.use_proxy)
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.post("/{media_asset_id}/render-optical-look", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optical_look(media_asset_id: UUID, payload: OpticalLookRequest, db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, payload.user_id)
    settings = payload.model_dump(mode="json", exclude={"user_id"})
    task = render_optical_look_preview.delay(str(asset.id), settings)
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")
