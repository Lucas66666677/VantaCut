from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, User
from app.schemas.optics import OpticalFlowRetimeRequest, OpticalLookRequest, OpticalTaskResponse, OpticsAnalysisRequest
from app.tasks.optical_tasks import analyze_optics, render_optical_look_preview, retime_with_optical_flow


router = APIRouter(prefix="/media", tags=["optics"])


def _authorise(db: Session, asset_id: UUID, current_user: User) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot process this media asset")
    return asset


@router.post("/{media_asset_id}/analyze-optics", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optics_analysis(media_asset_id: UUID, payload: OpticsAnalysisRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, current_user)
    task = analyze_optics.delay(str(asset.id))
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.post("/{media_asset_id}/retime-optical-flow", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optical_flow_retime(media_asset_id: UUID, payload: OpticalFlowRetimeRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, current_user)
    task = retime_with_optical_flow.delay(str(asset.id), payload.slow_motion_factor, payload.apply_motion_blur, payload.use_proxy)
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.post("/{media_asset_id}/render-optical-look", response_model=OpticalTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_optical_look(media_asset_id: UUID, payload: OpticalLookRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OpticalTaskResponse:
    asset = _authorise(db, media_asset_id, current_user)
    settings = payload.model_dump(mode="json")
    task = render_optical_look_preview.delay(str(asset.id), settings)
    return OpticalTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")
