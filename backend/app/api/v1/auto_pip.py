from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.auto_pip import AutoPipOverlayRequest, AutoPipOverlayResponse, AutoPipRequest, AutoPipResponse
from app.services.non_destructive import append_filter_layer
from app.tasks.auto_pip_tasks import configure_auto_pip


router = APIRouter(prefix="/timelines", tags=["auto-pip"])


def _timeline_for_user(db: Session, timeline_id: UUID, current_user: User) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    return timeline


@router.post("/{timeline_id}/auto-pip", response_model=AutoPipResponse, status_code=status.HTTP_202_ACCEPTED)
def request_auto_pip(
    timeline_id: UUID, payload: AutoPipRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoPipResponse:
    timeline = _timeline_for_user(db, timeline_id, current_user)
    task = configure_auto_pip.delay(str(timeline.id), str(payload.main_asset_id), str(payload.selfie_asset_id), payload.model_dump(mode="json", exclude={"main_asset_id", "selfie_asset_id"}))
    return AutoPipResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.put("/{timeline_id}/auto-pip/overlays", response_model=AutoPipOverlayResponse)
def add_auto_pip_overlay(
    timeline_id: UUID, payload: AutoPipOverlayRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoPipOverlayResponse:
    timeline = _timeline_for_user(db, timeline_id, current_user)
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=422, detail="Overlay end_time must be after start_time")
    settings = dict(timeline.settings_json or {}); auto_pip = dict(settings.get("auto_pip", {})); overlay_id = f"pip-overlay-{uuid4()}"
    overlay = {"id": overlay_id, **payload.model_dump(mode="json"), "animation": {"kind": "vector_draw", "draw_seconds": .28, "fade_out_seconds": .18}}
    auto_pip["overlays"] = [*list(auto_pip.get("overlays", [])), overlay][-200:]; settings["auto_pip"] = auto_pip
    effects = [item for item in settings.get("effect_tracks", []) if item.get("id") != "auto-pip-annotations"]
    effects.append({"id": "auto-pip-annotations", "type": "vector_overlay", "z_index": 90, "items": auto_pip["overlays"]}); settings["effect_tracks"] = effects
    timeline.settings_json = append_filter_layer(settings, kind="auto_pip_vector_overlay", target={"timeline_id": str(timeline.id), "overlay_id": overlay_id}, parameters=overlay, source="user")
    db.commit()
    return AutoPipOverlayResponse(overlay_id=overlay_id, status="saved")
