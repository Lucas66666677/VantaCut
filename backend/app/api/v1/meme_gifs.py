from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.meme_gifs import MemeGifRequest, MemeGifResponse, MemeGifStatusResponse
from app.tasks.meme_gif_tasks import generate_meme_gifs


router = APIRouter(prefix="/timelines", tags=["meme-gifs"])


def _source_asset_id(timeline: Timeline) -> UUID | None:
    confirmed = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    if isinstance(confirmed, dict) and confirmed.get("source_asset_id"):
        return UUID(str(confirmed["source_asset_id"]))
    for track in confirmed.get("tracks", []) if isinstance(confirmed, dict) else []:
        if track.get("type") == "main_video":
            for clip in track.get("clips", []):
                if clip.get("source_asset_id"):
                    return UUID(str(clip["source_asset_id"]))
    return None


@router.post("/{timeline_id}/meme-gifs", response_model=MemeGifResponse, status_code=status.HTTP_202_ACCEPTED)
def request_meme_gifs(timeline_id: UUID, payload: MemeGifRequest, db: Session = Depends(get_db)) -> MemeGifResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    source_id = payload.source_asset_id or _source_asset_id(timeline)
    source = db.get(MediaAsset, source_id) if source_id else None
    if source is None or source.project_id != timeline.project_id:
        raise HTTPException(status_code=422, detail="Select a source video from this project")
    asset_ids = [value for value in (payload.bgm_asset_id, payload.comedic_sfx_asset_id) if value]
    if asset_ids and db.query(MediaAsset).filter(MediaAsset.id.in_(asset_ids), MediaAsset.project_id == timeline.project_id).count() != len(set(asset_ids)):
        raise HTTPException(status_code=422, detail="BGM/SFX asset must belong to this project")
    settings = dict(timeline.settings_json or {})
    settings["meme_gif"] = {"status": "queued", "events": []}
    timeline.settings_json = settings
    db.commit()
    task = generate_meme_gifs.delay(str(timeline.id), str(source.id), payload.model_dump(mode="json", exclude={"user_id", "source_asset_id"}))
    return MemeGifResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.get("/{timeline_id}/meme-gifs", response_model=MemeGifStatusResponse)
def meme_gif_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> MemeGifStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("meme_gif", {}))
    return MemeGifStatusResponse(status=str(record.get("status", "idle")), events=list(record.get("events", [])), error=record.get("error"))
