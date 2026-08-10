from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.audio_delivery import (
    StemExtractionRequest, StemExtractionResponse, StemMixSettingsRequest, StemMixSettingsResponse,
)
from app.schemas.soundscape import SoundscapeGenerationRequest, SoundscapeTaskResponse
from app.tasks.stem_tasks import extract_stems
from app.tasks.soundscape_tasks import generate_soundscape_for_timeline


router = APIRouter(tags=["audio-delivery"])


def _assert_project_owner(db: Session, user_id: UUID, project_id: UUID) -> None:
    user = db.get(User, user_id)
    if user is None or not any(project.id == project_id for project in user.projects):
        raise HTTPException(status_code=403, detail="User cannot modify this project's audio")


@router.post("/media/{media_asset_id}/extract-stems", response_model=StemExtractionResponse, status_code=status.HTTP_202_ACCEPTED)
def request_stem_extraction(
    media_asset_id: UUID, payload: StemExtractionRequest, db: Session = Depends(get_db)
) -> StemExtractionResponse:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    _assert_project_owner(db, payload.user_id, asset.project_id)
    task = extract_stems.delay(str(asset.id), payload.model_name)
    return StemExtractionResponse(task_id=task.id, media_asset_id=asset.id, status="queued")


@router.put("/timelines/{timeline_id}/stem-mix", response_model=StemMixSettingsResponse)
def update_stem_mix(
    timeline_id: UUID, payload: StemMixSettingsRequest, db: Session = Depends(get_db)
) -> StemMixSettingsResponse:
    timeline = db.get(Timeline, timeline_id)
    asset = db.get(MediaAsset, payload.source_asset_id)
    if timeline is None or asset is None or asset.project_id != timeline.project_id:
        raise HTTPException(status_code=404, detail="Timeline or source asset not found in the same project")
    _assert_project_owner(db, payload.user_id, timeline.project_id)
    stems = dict((asset.metadata_json or {}).get("stems", {}))
    if stems.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Source asset has no completed stem extraction")
    timeline.settings_json = {
        **dict(timeline.settings_json or {}),
        "stem_mix": {
            "source_asset_id": str(asset.id), "status": "configured",
            "dialogue": payload.dialogue.model_dump(mode="json"),
            "music": payload.music.model_dump(mode="json"),
            "sfx": payload.sfx.model_dump(mode="json"),
        },
    }
    db.commit()
    return StemMixSettingsResponse(timeline_id=timeline.id, source_asset_id=asset.id, status="configured")


@router.post("/timelines/{timeline_id}/generate-soundscape", response_model=SoundscapeTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_soundscape(
    timeline_id: UUID, payload: SoundscapeGenerationRequest, db: Session = Depends(get_db)
) -> SoundscapeTaskResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    _assert_project_owner(db, payload.user_id, timeline.project_id)
    task = generate_soundscape_for_timeline.delay(str(timeline.id), payload.layout)
    return SoundscapeTaskResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
