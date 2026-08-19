from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, User
from app.schemas.rough_cut import RoughCutQueuedResponse, RoughCutRequest, RoughCutResultResponse
from app.tasks.audio_tasks import analyze_audio_rough_cut

router = APIRouter(prefix="/analysis", tags=["rough-cut"])


def _owned_asset(asset_id: UUID, current_user: User, db: Session) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot analyze this media asset")
    return asset


@router.post("/rough-cut", response_model=RoughCutQueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def request_rough_cut(
    payload: RoughCutRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> RoughCutQueuedResponse:
    asset = _owned_asset(payload.media_asset_id, current_user, db)
    if not asset.audio_key:
        raise HTTPException(status_code=409, detail="Media preprocessing has not produced an audio track")
    task = analyze_audio_rough_cut.delay(str(asset.id))
    return RoughCutQueuedResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.get("/rough-cut/{media_asset_id}", response_model=RoughCutResultResponse)
def get_rough_cut_result(
    media_asset_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> RoughCutResultResponse:
    asset = _owned_asset(media_asset_id, current_user, db)
    analysis = db.scalar(select(AIAnalysis).where(
        AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT, AIAnalysis.status == "completed",
    ).order_by(AIAnalysis.created_at.desc()))
    if analysis is None:
        raise HTTPException(status_code=404, detail="No completed rough-cut analysis found")
    result = dict(analysis.result_json or {})
    return RoughCutResultResponse(analysis_id=analysis.id, media_asset_id=asset.id, status=analysis.status, clip_analysis=list(result.get("clip_analysis", [])), timeline_suggestions=list(result.get("timeline_suggestions", [])))
