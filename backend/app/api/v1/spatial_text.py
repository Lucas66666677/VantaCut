from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.spatial_text import SpatialTextRequest, SpatialTrackingRequest, SpatialTrackingResponse
from app.services.non_destructive import append_filter_layer
from app.tasks.spatial_text_tasks import analyze_spatial_text

router = APIRouter(tags=["spatial-text"])

@router.post("/media/{media_asset_id}/analyze-spatial-text", response_model=SpatialTrackingResponse, status_code=status.HTTP_202_ACCEPTED)
def analyze(media_asset_id: UUID, payload: SpatialTrackingRequest, db: Session = Depends(get_db)) -> SpatialTrackingResponse:
    asset, user = db.get(MediaAsset, media_asset_id), db.get(User, payload.user_id)
    if asset is None or user is None or asset.project.owner_id != user.id: raise HTTPException(status_code=404, detail="Media asset not found")
    task = analyze_spatial_text.delay(str(asset.id), payload.use_proxy)
    return SpatialTrackingResponse(task_id=task.id, media_asset_id=asset.id, status="queued")

@router.post("/timelines/{timeline_id}/spatial-text")
def add_text(timeline_id: UUID, payload: SpatialTextRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    timeline, user, asset = db.get(Timeline, timeline_id), db.get(User, payload.user_id), db.get(MediaAsset, payload.source_asset_id)
    if timeline is None or user is None or asset is None or timeline.project.owner_id != user.id or asset.project_id != timeline.project_id: raise HTTPException(status_code=404, detail="Timeline or source asset not found")
    tracking = dict(asset.metadata_json or {}).get("spatial_text_tracking", {})
    if tracking.get("status") != "completed": raise HTTPException(status_code=409, detail="Analyze depth and camera tracking first")
    if payload.end_time <= payload.start_time: raise HTTPException(status_code=422, detail="end_time must be after start_time")
    item = {"id": f"spatial-text-{uuid4()}", **payload.model_dump(mode="json", exclude={"user_id"}), "depth_key": tracking["depth_key"], "camera_poses_key": tracking["camera_poses_key"], "occlusion_depth": round(1 - payload.z, 4), "animation": {"kind": "camera_locked", "z": payload.z}}
    settings = dict(timeline.settings_json or {}); effects = [entry for entry in settings.get("effect_tracks", []) if entry.get("id") != "spatial-text-track"]; effects.append({"id": "spatial-text-track", "type": "spatial_text", "z_index": 85, "items": [*list(dict(settings.get("spatial_text", {})).get("items", [])), item]}); settings["effect_tracks"] = effects; settings["spatial_text"] = {"items": effects[-1]["items"]}
    timeline.settings_json = append_filter_layer(settings, kind="spatial_text", target={"timeline_id": str(timeline.id), "source_asset_id": str(asset.id)}, parameters=item, source="user"); db.commit()
    return {"id": item["id"], "status": "saved"}
