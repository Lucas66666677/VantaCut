from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.visual_hooks import VisualHooksRequest, VisualHooksResponse
from app.services.hook_detector import analyze_opening_hook


router = APIRouter(prefix="/timelines", tags=["visual-hooks"])


def _duration(document: dict[str, object]) -> float:
    return sum(
        max(0.0, float(clip.get("source_end", 0)) - float(clip.get("source_start", 0)))
        for track in document.get("tracks", []) if isinstance(track, dict) and track.get("type") == "main_video"
        for clip in track.get("clips", []) if isinstance(clip, dict) and clip.get("action", "keep") == "keep"
    )


@router.put("/{timeline_id}/visual-hooks", response_model=VisualHooksResponse)
def configure_visual_hooks(timeline_id: UUID, payload: VisualHooksRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VisualHooksResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    settings = dict(timeline.settings_json or {}); document = dict(settings.get("confirmed_timeline", {})); duration = _duration(document)
    if duration <= 0:
        raise HTTPException(status_code=409, detail="Confirm a non-empty timeline before applying visual hooks")
    if not payload.enabled:
        settings["visual_hooks"] = {"status": "disabled", "style": payload.style, "platform": payload.platform}
        timeline.settings_json = settings; db.commit()
        return VisualHooksResponse(timeline_id=timeline.id, status="disabled", style=payload.style, platform=payload.platform)
    report = analyze_opening_hook(db, timeline); candidate = dict(report.get("highlight_candidate", {})); highlight_time = float(candidate.get("timeline_start", 0.0))
    if payload.suspense_enabled and highlight_time >= 5.0:
        suspense_end = min(14.0, max(.8, highlight_time - .15), duration - .1)
        suspense = {"enabled": True, "start": .25, "end": suspense_end, "text": f"WAIT FOR IT · {round(highlight_time)}s"}
    else:
        suspense = {"enabled": False}
    track = {"id": "visual-hooks", "type": "effect_overlay", "z_index": 70, "clips": [{"id": "retention-progress", "kind": "visual_progress", "timeline_start": 0, "source_start": 0, "source_end": duration, "action": "keep", "reason": "Retention progress indicator"}, *([{"id": "wait-for-it", "kind": "suspense_text", "timeline_start": suspense["start"], "source_start": 0, "source_end": suspense["end"] - suspense["start"], "action": "keep", "reason": "Highlight anticipation"}] if suspense.get("enabled") else [])]}
    settings["hook_report"] = {"status": "completed", **report}
    settings["visual_hooks"] = {"status": "configured", "style": payload.style, "platform": payload.platform, "timeline_duration": duration, "highlight_time": highlight_time, "suspense": suspense, "track": track}
    settings["confirmed_timeline"] = {**document, "tracks": [item for item in document.get("tracks", []) if item.get("id") != track["id"]] + [track]}
    timeline.settings_json = settings; db.commit()
    return VisualHooksResponse(timeline_id=timeline.id, status="configured", style=payload.style, platform=payload.platform, highlight_time=highlight_time, suspense_text=suspense.get("text"))
