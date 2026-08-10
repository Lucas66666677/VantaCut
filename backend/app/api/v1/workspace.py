from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Project, User, WorkspacePreference
from app.schemas.workspace import WorkspacePreferenceResponse, WorkspacePreferenceUpdateRequest

router = APIRouter(prefix="/projects", tags=["workspace-preferences"])


def _owned_project(project_id: UUID, user_id: UUID, db: Session) -> Project:
    project, user = db.get(Project, project_id), db.get(User, user_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user is None or project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot access this workspace")
    return project


@router.get("/{project_id}/workspace", response_model=WorkspacePreferenceResponse)
def get_workspace_preference(project_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> WorkspacePreferenceResponse:
    _owned_project(project_id, user_id, db)
    preference = db.query(WorkspacePreference).filter_by(project_id=project_id, user_id=user_id).one_or_none()
    if preference is None:
        raise HTTPException(status_code=404, detail="No saved workspace preference")
    return WorkspacePreferenceResponse(project_id=project_id, layout=preference.layout_json)


@router.put("/{project_id}/workspace", response_model=WorkspacePreferenceResponse)
def save_workspace_preference(project_id: UUID, payload: WorkspacePreferenceUpdateRequest, db: Session = Depends(get_db)) -> WorkspacePreferenceResponse:
    _owned_project(project_id, payload.user_id, db)
    preference = db.query(WorkspacePreference).filter_by(project_id=project_id, user_id=payload.user_id).one_or_none()
    serialized = payload.layout.model_dump(mode="json")
    if preference is None:
        preference = WorkspacePreference(user_id=payload.user_id, project_id=project_id)
        db.add(preference)
    preference.layout_version = payload.layout.version
    preference.layout_json = serialized
    db.commit()
    return WorkspacePreferenceResponse(project_id=project_id, layout=serialized)
