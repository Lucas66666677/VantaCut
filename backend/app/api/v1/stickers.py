from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.stickers import RecommendStickersRequest, StickerResponse, StickerTransformRequest, ToggleAIStickersRequest
from app.services.sticker_recommendations import recommend_stickers
from app.services.sticker_assets import animated_sticker_webp


router = APIRouter(prefix="/timelines", tags=["ai-stickers"])
library_router = APIRouter(prefix="/sticker-library", tags=["ai-stickers"])


@library_router.get("/{sticker_id}.webp")
def get_builtin_sticker(sticker_id: str) -> Response:
    try:
        payload = animated_sticker_webp(sticker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=payload, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


def _owned(timeline_id: UUID, current_user: User, db: Session) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    return timeline


def _sticker_track(settings: dict[str, object]) -> dict[str, object] | None:
    return next((dict(track) for track in list(settings.get("effect_tracks", [])) if isinstance(track, dict) and track.get("id") == "ai-semantic-stickers"), None)


@router.post("/{timeline_id}/recommend-stickers", response_model=StickerResponse)
def create_sticker_recommendations(timeline_id: UUID, payload: RecommendStickersRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> StickerResponse:
    timeline = _owned(timeline_id, current_user, db); settings = dict(timeline.settings_json or {})
    cues = list(dict(settings.get("subtitles", {})).get("items", []))
    if not cues:
        raise HTTPException(status_code=409, detail="Generate timestamped subtitles before requesting sticker recommendations")
    items = recommend_stickers([dict(cue) for cue in cues if isinstance(cue, dict)])
    track = {"id": "ai-semantic-stickers", "type": "sticker_overlay", "z_index": 70, "enabled": payload.enabled, "items": items}
    settings["effect_tracks"] = [track_item for track_item in list(settings.get("effect_tracks", [])) if not (isinstance(track_item, dict) and track_item.get("id") == track["id"])] + [track]
    settings["ai_stickers"] = {"status": "completed", "enabled": payload.enabled, "items": items}
    timeline.settings_json = settings; db.commit()
    return StickerResponse(timeline_id=timeline.id, status="completed", enabled=payload.enabled, items=items)


@router.put("/{timeline_id}/ai-stickers/enabled", response_model=StickerResponse)
def toggle_ai_stickers(timeline_id: UUID, payload: ToggleAIStickersRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> StickerResponse:
    timeline = _owned(timeline_id, current_user, db); settings = dict(timeline.settings_json or {}); track = _sticker_track(settings)
    if track is None:
        raise HTTPException(status_code=404, detail="No AI sticker track exists")
    track["enabled"] = payload.enabled
    settings["effect_tracks"] = [track if isinstance(item, dict) and item.get("id") == track["id"] else item for item in list(settings.get("effect_tracks", []))]
    settings["ai_stickers"] = {**dict(settings.get("ai_stickers", {})), "enabled": payload.enabled, "items": list(track.get("items", []))}
    timeline.settings_json = settings; db.commit()
    return StickerResponse(timeline_id=timeline.id, status="updated", enabled=payload.enabled, items=list(track.get("items", [])))


@router.patch("/{timeline_id}/stickers/{sticker_id}", response_model=StickerResponse)
def update_sticker_transform(timeline_id: UUID, sticker_id: str, payload: StickerTransformRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> StickerResponse:
    timeline = _owned(timeline_id, current_user, db); settings = dict(timeline.settings_json or {}); track = _sticker_track(settings)
    if track is None:
        raise HTTPException(status_code=404, detail="No AI sticker track exists")
    found = False; items: list[dict[str, object]] = []
    for item in list(track.get("items", [])):
        sticker = dict(item)
        if sticker.get("id") == sticker_id:
            sticker.update({"position": {"x": payload.transform.x, "y": payload.transform.y}, "scale": payload.transform.scale, "rotation": payload.transform.rotation, "source": payload.source}); found = True
        items.append(sticker)
    if not found:
        raise HTTPException(status_code=404, detail="Sticker not found")
    track["items"] = items; settings["effect_tracks"] = [track if isinstance(item, dict) and item.get("id") == track["id"] else item for item in list(settings.get("effect_tracks", []))]
    settings["ai_stickers"] = {**dict(settings.get("ai_stickers", {})), "items": items}; timeline.settings_json = settings; db.commit()
    return StickerResponse(timeline_id=timeline.id, status="updated", enabled=bool(track.get("enabled", True)), items=items)
