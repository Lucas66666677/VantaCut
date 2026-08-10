from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Project, User
from app.schemas.one_click import OneClickGenerateRequest, OneClickGenerateResponse, OneClickTemplateResponse
from app.services.one_click_templates import OneClickTemplateError, get_template, list_templates, template_summary
from app.tasks.one_click_tasks import generate_one_click_video

router = APIRouter(prefix="/projects", tags=["one-click"])


@router.get("/one-click/templates", response_model=list[OneClickTemplateResponse])
def available_templates() -> list[dict[str, object]]:
    return [template_summary(item) for item in list_templates()]


@router.post("/{project_id}/one-click/generate", response_model=OneClickGenerateResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_one_click(project_id: UUID, payload: OneClickGenerateRequest, db: Session = Depends(get_db)) -> OneClickGenerateResponse:
    project, user = db.get(Project, project_id), db.get(User, payload.user_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user is None or project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot generate this project")
    try:
        get_template(payload.template_id)
    except OneClickTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assets = db.query(MediaAsset).filter(MediaAsset.id.in_(payload.media_asset_ids), MediaAsset.project_id == project_id).all()
    if len(assets) != len(set(payload.media_asset_ids)):
        raise HTTPException(status_code=422, detail="Every source asset must belong to the project")
    if payload.bgm_asset_id is not None and db.get(MediaAsset, payload.bgm_asset_id) not in assets:
        bgm = db.get(MediaAsset, payload.bgm_asset_id)
        if bgm is None or bgm.project_id != project_id:
            raise HTTPException(status_code=422, detail="BGM asset must belong to the project")
    task = generate_one_click_video.delay(str(project_id), str(user.id), payload.model_dump(mode="json"))
    return OneClickGenerateResponse(task_id=task.id, project_id=project_id, status="queued")
