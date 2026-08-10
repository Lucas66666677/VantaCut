from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, User
from app.schemas.spatial import SpatialReconstructionRequest, SpatialTaskResponse, VirtualCameraRenderRequest
from app.services.storage import create_download_url
from app.tasks.spatial_tasks import reconstruct_spatial_scene, render_spatial_virtual_camera


router = APIRouter(prefix="/media", tags=["spatial-reconstruction"])


def _owned(asset: MediaAsset, user_id: UUID, db: Session) -> None:
    user = db.get(User, user_id)
    if user is None or asset.project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot access this spatial scene")


def _response(task_id: str, asset: MediaAsset) -> SpatialTaskResponse:
    path = f"/api/v1/projects/{asset.project_id}/status"
    return SpatialTaskResponse(task_id=task_id, media_asset_id=asset.id, project_id=asset.project_id, status="queued", status_sse_path=path, status_websocket_path=f"{path}/ws")


@router.post("/{media_asset_id}/spatial-reconstruction", response_model=SpatialTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_spatial_reconstruction(media_asset_id: UUID, payload: SpatialReconstructionRequest, db: Session = Depends(get_db)) -> SpatialTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    _owned(asset, payload.user_id, db)
    if asset.status != MediaStatus.READY: raise HTTPException(status_code=409, detail="Media asset is not ready")
    task = reconstruct_spatial_scene.delay(str(asset.id), payload.model_dump(mode="json", exclude={"user_id"}))
    return _response(task.id, asset)


@router.get("/{media_asset_id}/spatial-scene")
def get_spatial_scene(media_asset_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> dict:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    _owned(asset, user_id, db)
    scene = dict((asset.metadata_json or {}).get("spatial_scene", {}))
    if not scene: raise HTTPException(status_code=404, detail="No spatial reconstruction exists for this asset")
    if scene.get("splat_ply_key"): scene["splat_url"] = create_download_url(str(scene["splat_ply_key"]), expires_in=900)
    if scene.get("camera_poses_key"): scene["camera_poses_url"] = create_download_url(str(scene["camera_poses_key"]), expires_in=900)
    return scene


@router.post("/{media_asset_id}/spatial-scene/render", response_model=SpatialTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_virtual_camera_render(media_asset_id: UUID, payload: VirtualCameraRenderRequest, db: Session = Depends(get_db)) -> SpatialTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    _owned(asset, payload.user_id, db)
    if dict((asset.metadata_json or {}).get("spatial_scene", {})).get("status") != "completed": raise HTTPException(status_code=409, detail="Spatial scene is not ready")
    task = render_spatial_virtual_camera.delay(str(asset.id), payload.model_dump(mode="json", exclude={"user_id"}))
    return _response(task.id, asset)
