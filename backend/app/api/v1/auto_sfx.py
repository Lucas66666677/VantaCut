from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.auto_sfx import AutoSFXRequest, AutoSFXResponse
from app.services.auto_sfx import derive_auto_sfx_events

router = APIRouter(prefix="/timelines", tags=["auto-sfx"])


@router.put("/{timeline_id}/auto-sfx", response_model=AutoSFXResponse)
def configure_auto_sfx(
    timeline_id: UUID, payload: AutoSFXRequest,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> AutoSFXResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None: raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id: raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    ids = [item for item in (payload.pop_asset_id, payload.whoosh_asset_id, payload.impact_asset_id, payload.bgm_asset_id) if item]
    assets = db.query(MediaAsset).filter(MediaAsset.id.in_(ids), MediaAsset.project_id == timeline.project_id).all() if ids else []
    if len(assets) != len(set(ids)): raise HTTPException(status_code=422, detail="Every SFX/BGM asset must belong to this project")
    asset_map = {kind: str(value) for kind, value in {"pop": payload.pop_asset_id, "whoosh": payload.whoosh_asset_id, "impact": payload.impact_asset_id}.items() if value}
    settings = dict(timeline.settings_json or {}); confirmed = dict(settings.get("confirmed_timeline", {}))
    if not confirmed: raise HTTPException(status_code=409, detail="Confirm a timeline before generating Auto-SFX")
    events = derive_auto_sfx_events(confirmed_timeline=confirmed, subtitles=dict(settings.get("subtitles", {})), transition_graph=dict(settings.get("transition_graph", confirmed.get("transition_graph", {}))), asset_map=asset_map)
    track = {"id": "auto-sfx", "type": "audio_overlay", "z_index": 20, "clips": [{**event, "source_start": 0, "source_end": event["duration"], "action": "keep", "audio_enabled": True} for event in events]}
    tracks = [item for item in confirmed.get("tracks", []) if item.get("id") != "auto-sfx"] + [track]
    settings["confirmed_timeline"] = {**confirmed, "tracks": tracks}
    settings["auto_sfx"] = {"status": "configured", "events": events, "asset_map": asset_map, "bgm_asset_id": str(payload.bgm_asset_id) if payload.bgm_asset_id else None, "bgm_volume": payload.bgm_volume, "ducking": {"enabled": payload.ducking_enabled, "threshold": .035, "ratio": 8, "attack_ms": 20, "release_ms": 280}}
    timeline.settings_json = settings; db.commit()
    return AutoSFXResponse(timeline_id=timeline.id, status="configured", event_count=len(events), track=track)
