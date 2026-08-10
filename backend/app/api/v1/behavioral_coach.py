from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline, User
from app.schemas.behavioral_coach import ApplyBehavioralCoachRequest, BehavioralCoachRequest, BehavioralCoachTaskResponse
from app.services.behavioral_coach import apply_coach_markers_to_timeline
from app.tasks.behavioral_coach_tasks import analyze_behavioral_coach


router = APIRouter(tags=["behavioral-coach"])


def _owner(db: Session, asset: MediaAsset, user_id: UUID) -> None:
    user = db.get(User, user_id)
    if user is None or asset.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot access this coaching report")


@router.post("/media/{media_asset_id}/analyze-behavioral-coach", response_model=BehavioralCoachTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_behavioral_coach(media_asset_id: UUID, payload: BehavioralCoachRequest, db: Session = Depends(get_db)) -> BehavioralCoachTaskResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    _owner(db, asset, payload.user_id)
    if payload.timeline_id:
        timeline = db.get(Timeline, payload.timeline_id)
        if timeline is None or timeline.project_id != asset.project_id: raise HTTPException(status_code=422, detail="Timeline must belong to the media project")
    task = analyze_behavioral_coach.delay(str(asset.id), str(payload.timeline_id) if payload.timeline_id else None)
    return BehavioralCoachTaskResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.get("/media/{media_asset_id}/behavioral-coach-report")
def behavioral_coach_report(media_asset_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> dict:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None: raise HTTPException(status_code=404, detail="Media asset not found")
    _owner(db, asset, user_id)
    analysis = db.scalar(select(AIAnalysis).where(
        AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.SPEAKER_STATE,
        AIAnalysis.model_name == "behavioral_coach_v1", AIAnalysis.status == "completed",
    ).order_by(AIAnalysis.created_at.desc()))
    if analysis is None: raise HTTPException(status_code=404, detail="Behavioral coaching report not found")
    return {"analysis_id": str(analysis.id), **dict(analysis.result_json or {})}


@router.post("/timelines/{timeline_id}/apply-behavioral-coach", status_code=status.HTTP_204_NO_CONTENT)
def apply_behavioral_coach(timeline_id: UUID, payload: ApplyBehavioralCoachRequest, db: Session = Depends(get_db)) -> None:
    timeline, analysis = db.get(Timeline, timeline_id), db.get(AIAnalysis, payload.analysis_id)
    if timeline is None or analysis is None: raise HTTPException(status_code=404, detail="Timeline or coaching report not found")
    user = db.get(User, payload.user_id)
    if user is None or timeline.project.owner_id != user.id or analysis.media_asset.project_id != timeline.project_id:
        raise HTTPException(status_code=403, detail="User cannot apply this coaching report")
    if analysis.model_name != "behavioral_coach_v1" or analysis.status != "completed":
        raise HTTPException(status_code=409, detail="Coaching analysis is not completed")
    apply_coach_markers_to_timeline(timeline, dict(analysis.result_json or {})); db.commit()
