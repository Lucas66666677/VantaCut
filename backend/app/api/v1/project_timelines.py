"""Turning an uploaded asset into the timeline every export route needs.

`projects.py` closed the first gap in the public journey: nothing created a
`Project`, so a person who registered could not upload. This closes the next
one. Upload now works, and then stops: `POST /timelines/{id}/render`, the
omnichannel export, and the download URL are all keyed by a timeline id, and no
route in `app/api/v1` produced one. Every `Timeline(...)` in the backend lives
inside a Celery task or an AI pipeline — auto-director, one-click templates,
beat sync, auto-narration, long-to-shorts, live camera ingest — or clones a
timeline that already exists. There was no first one to clone.

Two routes, deliberately the smallest surface that unblocks export: make the
first cut of an asset you uploaded, and list what you already have. Trimming,
reordering and multi-track editing are real features and none of them is needed
to get an uploaded video to an export.

Ownership is taken from the verified token and never from the request, and the
project lookup is filtered by `owner_id` rather than checked afterwards, the
way `projects.py` and `project_status.py` already do it. That is not a
formality in this repository: routes here still accept a client-supplied
`user_id` (see `artifacts/service-readiness/vantacut-auth-route-map.md`), and a
new route repeating that pattern would let anyone create a timeline — and
request a render — inside someone else's project.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User
from app.schemas.project_timeline import ProjectTimelineCreateRequest, ProjectTimelineResponse
from app.services.timeline_bootstrap import TimelineBootstrapError, build_initial_confirmed_timeline

router = APIRouter(prefix="/projects", tags=["timelines"])

#: Same reasoning as `MAX_LISTED_PROJECTS`: a person returning to the studio
#: should see what they already have rather than accumulate empty versions.
#: A bound on the first call, not a pagination contract.
MAX_LISTED_TIMELINES = 100


def _owned_project(project_id: UUID, current_user: User, db: Session) -> Project:
    """The caller's project, or a 404 that says nothing about what exists.

    Filtered on `owner_id` in the query rather than fetched and compared, so
    someone else's project is invisible rather than forbidden.
    """
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post(
    "/{project_id}/timelines",
    response_model=ProjectTimelineResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_timeline(
    project_id: UUID,
    payload: ProjectTimelineCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectTimelineResponse:
    """Create the first cut of an uploaded asset: the whole clip, kept.

    The asset is looked up inside the project rather than globally, so an id
    belonging to another project cannot be pulled into this one — the same
    check `subtitles.py` makes before it writes a `confirmed_timeline`.
    """
    project = _owned_project(project_id, current_user, db)

    asset = db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == payload.source_asset_id, MediaAsset.project_id == project.id
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found in this project")
    if asset.media_type == MediaType.IMAGE:
        # An image has no duration, so it cannot be the source of a timed cut.
        # It can still be placed on a timeline built from a video.
        raise HTTPException(
            status_code=422, detail="A timeline needs a video or audio source, not an image"
        )
    if asset.status != MediaStatus.READY:
        # 409 rather than 422: nothing about the request is wrong, the asset is
        # simply not finished being probed. `process_new_media` fills in
        # `duration_seconds`, and the client should retry rather than change
        # anything. FAILED is included on purpose -- retrying is still the only
        # thing the client can do, and saying "still uploading" for a failed
        # probe would be a lie.
        raise HTTPException(
            status_code=409,
            detail=f"Media asset is {asset.status.value}; a timeline needs a ready source",
        )

    try:
        confirmed_timeline = build_initial_confirmed_timeline(
            source_asset_id=asset.id, duration_seconds=asset.duration_seconds
        )
    except TimelineBootstrapError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    next_version = int(
        db.scalar(select(func.max(Timeline.version)).where(Timeline.project_id == project.id)) or 0
    ) + 1
    # Demote first, then promote -- the idiom `app/services/hook_detector.py`
    # already uses. Several routes (`agent.py`, `academic.py`, `lecturas.py`)
    # refuse to act on a timeline that is not current, so leaving two current
    # versions behind would make which one they accept depend on row order.
    db.query(Timeline).filter(
        Timeline.project_id == project.id, Timeline.is_current.is_(True)
    ).update({Timeline.is_current: False}, synchronize_session=False)

    timeline = Timeline(
        project_id=project.id,
        name=payload.name.strip() or "未命名時間軸",
        version=next_version,
        is_current=True,
        settings_json={"confirmed_timeline": confirmed_timeline},
    )
    db.add(timeline)
    db.commit()
    db.refresh(timeline)
    return ProjectTimelineResponse.model_validate(timeline)


@router.get("/{project_id}/timelines", response_model=list[ProjectTimelineResponse])
def list_project_timelines(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=MAX_LISTED_TIMELINES),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectTimelineResponse]:
    """The timelines of a project the caller owns, newest version first."""
    project = _owned_project(project_id, current_user, db)
    timelines = db.scalars(
        select(Timeline)
        .where(Timeline.project_id == project.id)
        .order_by(Timeline.version.desc())
        .limit(limit)
    ).all()
    return [ProjectTimelineResponse.model_validate(timeline) for timeline in timelines]


__all__ = ["MAX_LISTED_TIMELINES", "router"]
