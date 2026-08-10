from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, SubscriptionTier, Timeline, User
from app.schemas.long_to_shorts import LongToShortsBatchRequest, LongToShortsRequest, LongToShortsResponse, LongToShortsStatusResponse
from app.services.storage import create_download_url
from app.tasks.long_to_shorts_tasks import export_long_to_shorts_batch, generate_long_to_shorts


router = APIRouter(prefix="/timelines", tags=["long-to-shorts"])


@router.post("/{timeline_id}/long-to-shorts", response_model=LongToShortsResponse, status_code=status.HTTP_202_ACCEPTED)
def request_long_to_shorts(timeline_id: UUID, payload: LongToShortsRequest, db: Session = Depends(get_db)) -> LongToShortsResponse:
    timeline, user, asset = db.get(Timeline, timeline_id), db.get(User, payload.user_id), db.get(MediaAsset, payload.source_media_asset_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if asset is None or asset.project_id != timeline.project_id or asset.status != MediaStatus.READY or not asset.proxy_key:
        raise HTTPException(status_code=422, detail="Select a ready project video with a generated proxy")
    settings = dict(timeline.settings_json or {}); settings["long_to_shorts"] = {"status": "queued", "source_asset_id": str(asset.id), "shorts": []}; timeline.settings_json = settings; db.commit()
    task = generate_long_to_shorts.delay(str(timeline.id), payload.model_dump(mode="json"))
    return LongToShortsResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.post("/{timeline_id}/long-to-shorts/export", response_model=LongToShortsResponse, status_code=status.HTTP_202_ACCEPTED)
def export_long_to_shorts(timeline_id: UUID, payload: LongToShortsBatchRequest, db: Session = Depends(get_db)) -> LongToShortsResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot export this timeline")
    record = dict((timeline.settings_json or {}).get("long_to_shorts", {}))
    if record.get("status") != "completed" or len(record.get("shorts", [])) != 3:
        raise HTTPException(status_code=409, detail="Generate three Shorts before exporting")
    # Keep the existing free-tier render-credit gate intact for a batch of three jobs.
    if user.subscription_tier == SubscriptionTier.FREE:
        if user.render_credits < 3:
            raise HTTPException(status_code=402, detail="免費版需要 3 點渲染點數才能批量導出 3 支 Shorts")
        user.render_credits -= 3
        db.commit()
    task = export_long_to_shorts_batch.delay(str(timeline.id), str(user.id), payload.resolution)
    return LongToShortsResponse(task_id=task.id, timeline_id=timeline.id, status="rendering")


@router.get("/{timeline_id}/long-to-shorts", response_model=LongToShortsStatusResponse)
def long_to_shorts_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> LongToShortsStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict((timeline.settings_json or {}).get("long_to_shorts", {})); asset = db.get(MediaAsset, UUID(str(record["source_asset_id"]))) if record.get("source_asset_id") else None
    return LongToShortsStatusResponse(status=str(record.get("status", "idle")), shorts=list(record.get("shorts", [])), download_url=create_download_url(str(record["zip_key"]), attachment_filename="shorts.zip") if record.get("zip_key") else None, source_preview_url=create_download_url(asset.proxy_key) if asset and asset.proxy_key else None, error=record.get("error"))
