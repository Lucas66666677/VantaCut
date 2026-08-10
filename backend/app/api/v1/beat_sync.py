from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Project, Timeline, User
from app.schemas.beat_sync import BeatSyncMontageRequest, BeatSyncMontageResponse, BeatSyncRequest, BeatSyncResponse
from app.tasks.beat_sync_tasks import analyze_and_plan, generate_montage


router = APIRouter(prefix="/timelines", tags=["beat-sync"])
montage_router = APIRouter(prefix="/projects", tags=["beat-sync"])


@router.post("/{timeline_id}/beat-sync/analyze", response_model=BeatSyncResponse, status_code=status.HTTP_202_ACCEPTED)
def request_beat_sync_analysis(timeline_id: UUID, payload: BeatSyncRequest, db: Session = Depends(get_db)) -> BeatSyncResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    user = db.get(User, payload.user_id)
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    bgm, source = db.get(MediaAsset, payload.bgm_asset_id), db.get(MediaAsset, payload.source_asset_id)
    if bgm is None or source is None or bgm.project_id != timeline.project_id or source.project_id != timeline.project_id:
        raise HTTPException(status_code=422, detail="BGM and source media must belong to the timeline project")
    task = analyze_and_plan.delay(str(timeline.id), str(bgm.id), str(source.id), payload.max_cut_suggestions, payload.detect_drops)
    return BeatSyncResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@montage_router.post("/{project_id}/beat-sync/montage", response_model=BeatSyncMontageResponse, status_code=status.HTTP_202_ACCEPTED)
def request_beat_montage(project_id: UUID, payload: BeatSyncMontageRequest, db: Session = Depends(get_db)) -> BeatSyncMontageResponse:
    project, user = db.get(Project, project_id), db.get(User, payload.user_id)
    if project is None: raise HTTPException(status_code=404, detail="Project not found")
    if user is None or project.owner_id != user.id: raise HTTPException(status_code=403, detail="User cannot modify this project")
    assets = db.query(MediaAsset).filter(MediaAsset.id.in_(payload.media_asset_ids), MediaAsset.project_id == project.id).all()
    bgm = db.get(MediaAsset, payload.bgm_asset_id)
    if len(set(payload.media_asset_ids)) != len(payload.media_asset_ids) or len(assets) != len(payload.media_asset_ids) or bgm is None or bgm.project_id != project.id:
        raise HTTPException(status_code=422, detail="BGM and every selected media asset must belong to the project; duplicate media is not supported")
    task = generate_montage.delay(str(project.id), str(user.id), payload.model_dump(mode="json", exclude={"user_id"}))
    return BeatSyncMontageResponse(task_id=task.id, project_id=project.id, status="queued")
