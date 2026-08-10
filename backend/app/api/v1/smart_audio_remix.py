from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, Timeline, User
from app.schemas.smart_audio_remix import SmartAudioRemixRequest, SmartAudioRemixResponse, SmartAudioRemixStatusResponse
from app.tasks.smart_audio_remix_tasks import generate_smart_audio_remix


router = APIRouter(prefix="/timelines", tags=["smart-audio-remix"])


def _timeline_duration(document: dict[str, object]) -> float:
    clips = [clip for track in document.get("tracks", []) if isinstance(track, dict) and track.get("type") == "main_video" for clip in track.get("clips", []) if isinstance(clip, dict) and clip.get("action", "keep") == "keep"]
    return sum(max(0.0, float(clip.get("source_end", 0)) - float(clip.get("source_start", 0))) for clip in clips)


def _configured_bgm_id(settings: dict[str, object]) -> UUID | None:
    for key in ("smart_audio_remix", "auto_sfx", "one_click", "beat_sync_montage", "auto_narrative"):
        candidate = dict(settings.get(key, {}) or {}).get("bgm_asset_id")
        if candidate:
            return UUID(str(candidate))
    return None


@router.post("/{timeline_id}/smart-audio-remix", response_model=SmartAudioRemixResponse, status_code=status.HTTP_202_ACCEPTED)
def request_smart_audio_remix(timeline_id: UUID, payload: SmartAudioRemixRequest, db: Session = Depends(get_db)) -> SmartAudioRemixResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    settings = dict(timeline.settings_json or {}); document = dict(settings.get("confirmed_timeline", {}))
    duration = payload.target_duration_seconds or _timeline_duration(document)
    if duration <= 0:
        raise HTTPException(status_code=409, detail="Confirm a non-empty timeline before remixing BGM")
    bgm_id = payload.bgm_asset_id or _configured_bgm_id(settings)
    bgm = db.get(MediaAsset, bgm_id) if bgm_id else None
    if bgm is None or bgm.project_id != timeline.project_id or bgm.status != MediaStatus.READY:
        raise HTTPException(status_code=422, detail="Select a ready project BGM, or configure one on the timeline first")
    settings["smart_audio_remix"] = {"status": "queued", "bgm_asset_id": str(bgm.id), "target_duration_seconds": duration, "mix_level": payload.mix_level}
    timeline.settings_json = settings; db.commit()
    task = generate_smart_audio_remix.delay(str(timeline.id), str(bgm.id), {"target_duration_seconds": duration, "mix_level": payload.mix_level})
    return SmartAudioRemixResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.get("/{timeline_id}/smart-audio-remix", response_model=SmartAudioRemixStatusResponse)
def smart_audio_remix_status(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> SmartAudioRemixStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this timeline")
    record = dict(dict(timeline.settings_json or {}).get("smart_audio_remix", {}))
    return SmartAudioRemixStatusResponse(status=str(record.get("status", "idle")), target_duration_seconds=record.get("target_duration_seconds"), bpm=record.get("bpm"), sections=list(record.get("sections", [])), error=record.get("error"))


@router.delete("/{timeline_id}/smart-audio-remix", response_model=SmartAudioRemixStatusResponse)
def disable_smart_audio_remix(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> SmartAudioRemixStatusResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    settings = dict(timeline.settings_json or {}); record = dict(settings.get("smart_audio_remix", {}))
    record["status"] = "disabled"; settings["smart_audio_remix"] = record; timeline.settings_json = settings; db.commit()
    return SmartAudioRemixStatusResponse(status="disabled", target_duration_seconds=record.get("target_duration_seconds"), bpm=record.get("bpm"), sections=list(record.get("sections", [])))
