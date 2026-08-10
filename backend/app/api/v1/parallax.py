from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, User
from app.schemas.parallax import ParallaxLayerRequest, ParallaxLayerTaskResponse
from app.tasks.parallax_tasks import generate_layers


router = APIRouter(tags=["parallax-zoom"])


@router.post("/media/{media_asset_id}/generate-parallax-layers", response_model=ParallaxLayerTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_parallax_layers(
    media_asset_id: UUID, payload: ParallaxLayerRequest, db: Session = Depends(get_db),
) -> ParallaxLayerTaskResponse:
    asset, user = db.get(MediaAsset, media_asset_id), db.get(User, payload.user_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if user is None or asset.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot generate parallax layers for this asset")
    task = generate_layers.delay(str(asset.id), payload.depth_model, payload.use_proxy)
    project_path = f"/api/v1/projects/{asset.project_id}/status"
    return ParallaxLayerTaskResponse(
        task_id=task.id, media_asset_id=asset.id, project_id=asset.project_id, status="queued",
        status_sse_path=f"{project_path}/stream", status_websocket_path=f"{project_path}/ws",
    )
