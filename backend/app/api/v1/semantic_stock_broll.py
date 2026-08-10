from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, Timeline, User
from app.schemas.stock_broll import SemanticStockBRollRequest, SemanticStockBRollStatusResponse, SemanticStockBRollTaskResponse
from app.tasks.stock_broll_tasks import generate_semantic_stock_broll


router = APIRouter(prefix="/timelines", tags=["semantic-stock-broll"])


@router.post("/{timeline_id}/b-roll/semantic-stock", response_model=SemanticStockBRollTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_semantic_stock_broll(timeline_id: UUID, payload: SemanticStockBRollRequest, db: Session = Depends(get_db)) -> SemanticStockBRollTaskResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    asset = db.get(MediaAsset, payload.source_asset_id)
    if asset is None or asset.project_id != timeline.project_id or asset.status != MediaStatus.READY:
        raise HTTPException(status_code=422, detail="source_asset_id must be a ready project video")
    timeline.settings_json = {
        **dict(timeline.settings_json or {}),
        "semantic_stock_broll": {"status": "queued", "clips": []},
    }
    db.commit()
    task = generate_semantic_stock_broll.delay(str(timeline.id), payload.model_dump(mode="json", exclude={"user_id"}))
    base = f"/api/v1/projects/{timeline.project_id}/status"
    return SemanticStockBRollTaskResponse(task_id=task.id, project_id=timeline.project_id, status="queued", status_sse_path=base, status_websocket_path=f"{base}/ws")


@router.get("/{timeline_id}/b-roll/semantic-stock", response_model=SemanticStockBRollStatusResponse)
def semantic_stock_broll_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> SemanticStockBRollStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("semantic_stock_broll", {}))
    return SemanticStockBRollStatusResponse(status=str(record.get("status", "idle")), clips=list(record.get("clips", [])), error=record.get("error"))
