from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Clip, Timeline
from app.tasks.audio_enhancement_tasks import enhance_audio, enhance_studio_sound

router = APIRouter(prefix="/timelines", tags=["audio-enhancement"])


class AudioEnhancementResponse(BaseModel):
    task_id: str
    clip_id: UUID
    status: str


class StudioSoundRequest(BaseModel):
    wet_mix: int = Field(default=72, ge=0, le=100)


@router.post(
    "/{timeline_id}/clips/{clip_id}/noise-reduction",
    response_model=AudioEnhancementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_noise_reduction(
    timeline_id: UUID,
    clip_id: UUID,
    db: Session = Depends(get_db),
) -> AudioEnhancementResponse:
    timeline = db.get(Timeline, timeline_id)
    clip = db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")
    task = enhance_audio.delay(str(clip.id))
    return AudioEnhancementResponse(task_id=task.id, clip_id=clip.id, status="queued")


@router.delete("/{timeline_id}/clips/{clip_id}/noise-reduction", status_code=status.HTTP_204_NO_CONTENT)
def disable_noise_reduction(
    timeline_id: UUID,
    clip_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """Disable the render-time effect; the prior preview object can expire by lifecycle policy."""
    timeline = db.get(Timeline, timeline_id)
    clip = db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")

    clip.audio_effects = [effect for effect in clip.audio_effects if effect != "noise_reduction"]
    settings = dict(timeline.settings_json or {})
    effect_map = dict(settings.get("clip_audio_effects", {}))
    existing = dict(effect_map.get(str(clip.id), {}))
    existing["audio_effects"] = clip.audio_effects
    effect_map[str(clip.id)] = existing
    updated_settings = {**settings, "clip_audio_effects": effect_map}
    for document_key in ("multitrack_timeline", "confirmed_timeline"):
        document = updated_settings.get(document_key)
        if not isinstance(document, dict):
            continue
        for track in document.get("tracks", []):
            for timeline_clip in track.get("clips", []):
                if str(timeline_clip.get("id")) == str(clip.id):
                    timeline_clip["audio_effects"] = clip.audio_effects
    timeline.settings_json = updated_settings
    db.commit()


@router.post("/{timeline_id}/clips/{clip_id}/studio-sound", response_model=AudioEnhancementResponse, status_code=status.HTTP_202_ACCEPTED)
def request_studio_sound(timeline_id: UUID, clip_id: UUID, payload: StudioSoundRequest, db: Session = Depends(get_db)) -> AudioEnhancementResponse:
    timeline, clip = db.get(Timeline, timeline_id), db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")
    wet_mix = max(0, min(100, payload.wet_mix))
    task = enhance_studio_sound.delay(str(clip.id), wet_mix)
    return AudioEnhancementResponse(task_id=task.id, clip_id=clip.id, status="queued")


@router.patch("/{timeline_id}/clips/{clip_id}/studio-sound", status_code=status.HTTP_204_NO_CONTENT)
def update_studio_sound_mix(timeline_id: UUID, clip_id: UUID, payload: StudioSoundRequest, db: Session = Depends(get_db)) -> None:
    timeline, clip = db.get(Timeline, timeline_id), db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")
    settings = dict(timeline.settings_json or {}); effect_map = dict(settings.get("clip_audio_effects", {})); entry = dict(effect_map.get(str(clip.id), {})); studio = dict(entry.get("studio_sound", {}))
    if not studio.get("enhanced_audio_key"):
        raise HTTPException(status_code=409, detail="Studio Sound must finish before its dry/wet mix can be changed")
    studio["wet_mix"] = max(0, min(100, payload.wet_mix)); entry["studio_sound"] = studio; effect_map[str(clip.id)] = entry
    timeline.settings_json = {**settings, "clip_audio_effects": effect_map}; db.commit()


@router.delete("/{timeline_id}/clips/{clip_id}/studio-sound", status_code=status.HTTP_204_NO_CONTENT)
def disable_studio_sound(timeline_id: UUID, clip_id: UUID, db: Session = Depends(get_db)) -> None:
    timeline, clip = db.get(Timeline, timeline_id), db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")
    clip.audio_effects = [effect for effect in clip.audio_effects if effect != "studio_sound"]
    settings = dict(timeline.settings_json or {}); effect_map = dict(settings.get("clip_audio_effects", {})); entry = dict(effect_map.get(str(clip.id), {})); entry.pop("studio_sound", None); entry["audio_effects"] = clip.audio_effects; effect_map[str(clip.id)] = entry
    timeline.settings_json = {**settings, "clip_audio_effects": effect_map}; db.commit()
