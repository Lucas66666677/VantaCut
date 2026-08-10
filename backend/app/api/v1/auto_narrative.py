from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, User
from app.schemas.auto_narrative import AutoNarrativeRequest, AutoNarrativeResponse
from app.tasks.auto_narrative_tasks import generate_auto_narrative


router = APIRouter(prefix="/projects", tags=["auto-narrative"])


@router.post("/{project_id}/auto-narrative", response_model=AutoNarrativeResponse, status_code=status.HTTP_202_ACCEPTED)
def request_auto_narrative(project_id: UUID, payload: AutoNarrativeRequest, db: Session = Depends(get_db)) -> AutoNarrativeResponse:
    project, user = db.get(Project, project_id), db.get(User, payload.user_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user is None or project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot generate this project")
    assets = db.query(MediaAsset).filter(MediaAsset.id.in_(payload.media_asset_ids), MediaAsset.project_id == project_id).all()
    if len(assets) != len(payload.media_asset_ids) or any(item.status != MediaStatus.READY or item.media_type != MediaType.VIDEO for item in assets):
        raise HTTPException(status_code=422, detail="Select 5-10 ready video assets from this project")
    if payload.bgm_asset_id:
        bgm = db.get(MediaAsset, payload.bgm_asset_id)
        if bgm is None or bgm.project_id != project_id or bgm.status != MediaStatus.READY:
            raise HTTPException(status_code=422, detail="The selected Lo-Fi BGM must be a ready project asset")
    task = generate_auto_narrative.delay(str(project_id), payload.model_dump(mode="json"))
    return AutoNarrativeResponse(task_id=task.id, project_id=project_id, status="queued")
