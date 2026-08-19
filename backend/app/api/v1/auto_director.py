from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import AutoDirectorRun, AutoDirectorStatus, Project, User
from app.schemas.auto_director import (
    AutoDirectorCreateRequest, AutoDirectorCreateResponse, AutoDirectorRunResponse,
)
from app.tasks.auto_director_tasks import create_documentary


router = APIRouter(tags=["auto-director"])


def _authorise_project(db: Session, project_id: UUID, current_user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot run Auto Director for this project")
    return project


@router.post("/projects/{project_id}/auto-director", response_model=AutoDirectorCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def start_auto_director(
    project_id: UUID, payload: AutoDirectorCreateRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoDirectorCreateResponse:
    _authorise_project(db, project_id, current_user)
    run = AutoDirectorRun(
        project_id=project_id,
        requested_by_id=current_user.id,
        topic=payload.topic,
        creative_brief_json=payload.creative_brief(),
        status=AutoDirectorStatus.QUEUED,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        task = create_documentary.delay(str(run.id))
    except Exception as exc:
        run.status = AutoDirectorStatus.FAILED
        run.error_message = f"Unable to enqueue Auto Director: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail="Auto Director queue is temporarily unavailable") from exc
    return AutoDirectorCreateResponse(run_id=run.id, task_id=task.id, status=run.status.value)


@router.get("/auto-director/{run_id}", response_model=AutoDirectorRunResponse)
def get_auto_director_run(
    run_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoDirectorRunResponse:
    run = db.get(AutoDirectorRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Auto Director run not found")
    _authorise_project(db, run.project_id, current_user)
    return AutoDirectorRunResponse(
        id=run.id,
        project_id=run.project_id,
        topic=run.topic,
        status=run.status.value,
        provider_name=run.provider_name,
        script=run.script_json or {},
        research=run.research_json or {},
        narration_key=run.narration_key,
        result_timeline_id=run.result_timeline_id,
        message=run.error_message,
    )
