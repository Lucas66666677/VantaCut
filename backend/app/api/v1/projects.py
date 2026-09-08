"""Creating and listing the projects a signed-in person owns.

Every later leg of the public journey is scoped to a project: `/media/*` takes
a `project_id` and 404s when it is absent, a render is requested on a timeline
that belongs to one, and a download URL is authorised through
`job.project.owner_id`. Until now nothing created one. The only `Project(...)`
in the backend was in `app/tasks/platform_tasks.py`, behind the headless
Platform API whose keys need `X-Platform-Admin-Token`, and the repository's own
QA fixture inserted the row with a direct database session because no route
existed. A person who registered could not upload anything.

These two routes close that. They are deliberately the smallest surface that
unblocks the journey -- create one, list your own -- rather than a project
management API: rename, delete, transfer and sharing are all real features and
none of them is needed to record an interview's worth of media.

Ownership is enforced the way `project_status.py` already does it: the listing
filters on `owner_id`, so a project belonging to someone else is not visible
rather than forbidden, and no route here takes an id at all.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Project, User
from app.schemas.project import ProjectCreateRequest, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])

#: A person opening the studio on a second device should see what they already
#: have rather than accumulate empty workspaces, so the client lists first. The
#: cap keeps that first call bounded; it is not a pagination contract.
MAX_LISTED_PROJECTS = 100


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Create a project owned by the caller.

    The owner is taken from the verified token and never from the request
    body. That is not a formality here: this repository still has routes that
    accept a client-supplied `user_id` (see
    `artifacts/service-readiness/vantacut-auth-route-map.md`), and a new route
    that repeated the pattern would hand anyone the ability to create a project
    in someone else's account -- and, through it, an upload target.
    """
    request = payload or ProjectCreateRequest()
    project = Project(
        owner_id=current_user.id,
        name=request.name.strip() or "未命名專案",
        description=request.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    limit: int = Query(default=20, ge=1, le=MAX_LISTED_PROJECTS),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectResponse]:
    """The caller's own projects, most recently updated first.

    Filtered by `owner_id` in the query rather than checked afterwards: a
    filter cannot be forgotten for one branch the way a post-hoc comparison
    can, and it means another person's project is invisible rather than
    refused, which tells an unauthorised caller nothing about what exists.
    """
    projects = db.scalars(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.updated_at.desc())
        .limit(limit)
    ).all()
    return [ProjectResponse.model_validate(project) for project in projects]


__all__ = ["MAX_LISTED_PROJECTS", "router"]
