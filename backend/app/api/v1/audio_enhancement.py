from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Clip, Timeline, User
from app.tasks.audio_enhancement_tasks import enhance_audio, enhance_studio_sound

router = APIRouter(prefix="/timelines", tags=["audio-enhancement"])


class AudioEnhancementResponse(BaseModel):
    task_id: str
    clip_id: UUID
    status: str


class StudioSoundRequest(BaseModel):
    wet_mix: int = Field(default=72, ge=0, le=100)


def _clip_not_found() -> HTTPException:
    # One response shape for every negative case (timeline missing, timeline
    # not owned by this caller, clip missing, clip belongs to a *different*
    # timeline) — a caller probing for a valid-looking timeline/clip pairing
    # gets no signal about which part of the pairing was wrong.
    return HTTPException(status_code=404, detail="Timeline clip not found")


def _authorize_timeline_clip(db: Session, timeline_id: UUID, clip_id: UUID, current_user: User) -> tuple[Timeline, Clip]:
    """Resolve and authorize timeline_id + clip_id together.

    Ownership is checked on the timeline first (cheap, no cross-resource
    trust yet), and only THEN is clip_id resolved and cross-checked against
    that specific timeline. This defends against cross-resource IDOR: a
    caller who legitimately owns timeline A cannot reach a clip that
    actually belongs to timeline B by pairing A's timeline_id with B's
    clip_id in the URL — `clip.timeline_id != timeline.id` rejects that
    combination even though both ids individually resolve to real rows
    (timeline A really is this caller's; clip from B really exists, just not
    on this timeline).
    """
    timeline = db.get(Timeline, timeline_id)
    if timeline is None or timeline.project.owner_id != current_user.id:
        raise _clip_not_found()
    clip = db.get(Clip, clip_id)
    if clip is None or clip.timeline_id != timeline.id:
        raise _clip_not_found()
    return timeline, clip


@router.post(
    "/{timeline_id}/clips/{clip_id}/noise-reduction",
    response_model=AudioEnhancementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_noise_reduction(
    timeline_id: UUID,
    clip_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AudioEnhancementResponse:
    _timeline, clip = _authorize_timeline_clip(db, timeline_id, clip_id, current_user)
    task = enhance_audio.delay(str(clip.id))
    return AudioEnhancementResponse(task_id=task.id, clip_id=clip.id, status="queued")


@router.delete("/{timeline_id}/clips/{clip_id}/noise-reduction", status_code=status.HTTP_204_NO_CONTENT)
def disable_noise_reduction(
    timeline_id: UUID,
    clip_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Disable the render-time effect; the prior preview object can expire by lifecycle policy."""
    timeline, clip = _authorize_timeline_clip(db, timeline_id, clip_id, current_user)

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
def request_studio_sound(
    timeline_id: UUID,
    clip_id: UUID,
    payload: StudioSoundRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AudioEnhancementResponse:
    _timeline, clip = _authorize_timeline_clip(db, timeline_id, clip_id, current_user)
    wet_mix = max(0, min(100, payload.wet_mix))
    task = enhance_studio_sound.delay(str(clip.id), wet_mix)
    return AudioEnhancementResponse(task_id=task.id, clip_id=clip.id, status="queued")


@router.patch("/{timeline_id}/clips/{clip_id}/studio-sound", status_code=status.HTTP_204_NO_CONTENT)
def update_studio_sound_mix(
    timeline_id: UUID,
    clip_id: UUID,
    payload: StudioSoundRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    timeline, clip = _authorize_timeline_clip(db, timeline_id, clip_id, current_user)
    settings = dict(timeline.settings_json or {}); effect_map = dict(settings.get("clip_audio_effects", {})); entry = dict(effect_map.get(str(clip.id), {})); studio = dict(entry.get("studio_sound", {}))
    if not studio.get("enhanced_audio_key"):
        raise HTTPException(status_code=409, detail="Studio Sound must finish before its dry/wet mix can be changed")
    studio["wet_mix"] = max(0, min(100, payload.wet_mix)); entry["studio_sound"] = studio; effect_map[str(clip.id)] = entry
    timeline.settings_json = {**settings, "clip_audio_effects": effect_map}; db.commit()


@router.delete("/{timeline_id}/clips/{clip_id}/studio-sound", status_code=status.HTTP_204_NO_CONTENT)
def disable_studio_sound(
    timeline_id: UUID,
    clip_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    timeline, clip = _authorize_timeline_clip(db, timeline_id, clip_id, current_user)
    clip.audio_effects = [effect for effect in clip.audio_effects if effect != "studio_sound"]
    settings = dict(timeline.settings_json or {}); effect_map = dict(settings.get("clip_audio_effects", {})); entry = dict(effect_map.get(str(clip.id), {})); entry.pop("studio_sound", None); entry["audio_effects"] = clip.audio_effects; effect_map[str(clip.id)] = entry
    timeline.settings_json = {**settings, "clip_audio_effects": effect_map}; db.commit()
