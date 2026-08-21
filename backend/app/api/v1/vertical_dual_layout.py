from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.vertical_dual_layout import VerticalDualLayoutRequest, VerticalDualLayoutResponse
from app.tasks.vertical_dual_layout_tasks import analyze_vertical_dual_layout_task


router = APIRouter(prefix="/timelines", tags=["vertical-dual-layout"])


def _timeline_source_asset_id(timeline: Timeline) -> UUID | None:
    document = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    if isinstance(document, dict) and document.get("source_asset_id"):
        return UUID(str(document["source_asset_id"]))
    for track in document.get("tracks", []) if isinstance(document, dict) else []:
        clips = track.get("clips", []) if isinstance(track, dict) else []
        if track.get("type") == "main_video" and clips and clips[0].get("source_asset_id"):
            return UUID(str(clips[0]["source_asset_id"]))
    return None


@router.post("/{timeline_id}/vertical-dual-layout", response_model=VerticalDualLayoutResponse, status_code=status.HTTP_202_ACCEPTED)
def configure_vertical_dual_layout(timeline_id: UUID, payload: VerticalDualLayoutRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VerticalDualLayoutResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    source_id = payload.source_asset_id or _timeline_source_asset_id(timeline)
    source = db.get(MediaAsset, source_id) if source_id else None
    if source is None or source.project_id != timeline.project_id:
        raise HTTPException(status_code=422, detail="Select a source video from this timeline project")
    task = analyze_vertical_dual_layout_task.delay(str(timeline.id), str(source.id), payload.model_dump(mode="json", exclude={"user_id", "source_asset_id"}))
    return VerticalDualLayoutResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
